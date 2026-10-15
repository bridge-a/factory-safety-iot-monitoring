"""Optional CPU flame detection using the camera's already-oriented latest JPEG."""
import ast
from collections import deque
from dataclasses import dataclass
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import threading
import time

LOGGER = logging.getLogger("factory-vision")
MODEL_DIR = Path(__file__).resolve().parent / "models"


def model_manifest():
    return json.loads((MODEL_DIR / "fire-model.json").read_text(encoding="utf-8"))


def verified_model():
    manifest = model_manifest()
    path = MODEL_DIR / manifest["file"]
    if not path.is_file():
        raise ValueError("未找到模型文件，请先运行 enable-vision.sh")
    if path.stat().st_size != manifest["size"] or hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"]:
        raise ValueError("模型校验失败，请重新上传或下载模型")
    return path, manifest


@dataclass(frozen=True)
class VisionSettings:
    enabled: bool = False
    confidence: float = 0.55
    fps: float = 2.0
    confirm_seconds: float = 1.5
    stale_seconds: float = 3.0

    @classmethod
    def from_env(cls):
        enabled = os.environ.get("FACTORY_VISION_ENABLED", "0").lower()
        if enabled not in {"0", "1", "true", "false"}:
            raise ValueError("FACTORY_VISION_ENABLED 应为 0 或 1")
        result = cls(enabled=enabled in {"1", "true"},
                     confidence=float(os.environ.get("FACTORY_VISION_CONFIDENCE", "0.55")),
                     fps=float(os.environ.get("FACTORY_VISION_FPS", "2")),
                     confirm_seconds=float(os.environ.get("FACTORY_VISION_CONFIRM_SECONDS", "1.5")))
        if not (.1 <= result.confidence <= .95 and .5 <= result.fps <= 5 and .5 <= result.confirm_seconds <= 10):
            raise ValueError("视觉参数超出范围：置信度 0.1–0.95，帧率 0.5–5，确认时间 0.5–10 秒")
        return result


def letterbox(image, size=320):
    import numpy as np
    from PIL import Image
    width, height = image.size
    scale = min(size / width, size / height)
    resized = (round(width * scale), round(height * scale))
    left, top = (size - resized[0]) // 2, (size - resized[1]) // 2
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    canvas.paste(image.resize(resized, Image.Resampling.BILINEAR), (left, top))
    tensor = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    return np.ascontiguousarray(tensor), (scale, left, top)


def decode_fire(output, transform, image_size, confidence, iou_threshold=.45):
    """Pinned YOLOv8 output: xywh + fire score + smoke score, no objectness."""
    import numpy as np
    if output.shape != (1, 6, 2100):
        raise ValueError("模型输出形状不匹配")
    rows = output[0].T
    # A smoke-dominant prediction is not a flame alarm.
    rows = rows[np.isfinite(rows).all(axis=1) & (rows[:, 4] >= confidence) & (rows[:, 4] >= rows[:, 5])]
    scale, left, top = transform
    width, height = image_size
    candidates = []
    for x, y, w, h, score, _ in rows:
        box = [max(0., min(width, (x-w/2-left)/scale)), max(0., min(height, (y-h/2-top)/scale)),
               max(0., min(width, (x+w/2-left)/scale)), max(0., min(height, (y+h/2-top)/scale))]
        if box[2] > box[0] and box[3] > box[1]:
            candidates.append({"box": [float(v) for v in box], "confidence": float(score)})
    candidates.sort(key=lambda item: item["confidence"], reverse=True)
    kept = []
    for candidate in candidates[:300]:
        a = candidate["box"]
        duplicate = False
        for existing in kept:
            b = existing["box"]
            overlap = max(0., min(a[2], b[2])-max(a[0], b[0])) * max(0., min(a[3], b[3])-max(a[1], b[1]))
            union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - overlap
            if overlap / max(union, 1e-9) > iou_threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
        if len(kept) >= 20:
            break
    return kept


class FireModel:
    def __init__(self, confidence=.55):
        import onnxruntime as ort
        path, manifest = verified_model()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        model_input, model_output = self.session.get_inputs()[0], self.session.get_outputs()[0]
        names = ast.literal_eval(self.session.get_modelmeta().custom_metadata_map.get("names", "{}"))
        if (model_input.shape != manifest["input"] or model_input.type != "tensor(float)" or
                model_output.shape != manifest["output"] or names != {0: "fire", 1: "smoke"}):
            raise ValueError("模型输入输出或类别不匹配")
        self.input_name = model_input.name
        self.name = manifest["name"]
        self.confidence = confidence

    def infer(self, jpeg):
        from PIL import Image, ImageDraw, ImageFont
        with Image.open(io.BytesIO(jpeg)) as decoded:
            image = decoded.convert("RGB")
        tensor, transform = letterbox(image)
        output = self.session.run(None, {self.input_name: tensor})[0]
        detections = decode_fire(output, transform, image.size, self.confidence)
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=17)
        for item in detections:
            box = item["box"]
            draw.rectangle(box, outline=(226, 71, 51), width=3)
            label = f"FIRE {item['confidence']:.0%}"
            x, y = box[0], max(0, box[1]-24)
            draw.rectangle((x, y, min(image.width, x+105), y+22), fill=(174, 42, 30))
            draw.text((x+5, y+2), label, fill="white", font=font)
        rendered = io.BytesIO()
        image.save(rendered, format="JPEG", quality=85)
        return detections, rendered.getvalue()


class ConfirmationGate:
    def __init__(self, seconds=1.5, max_gap=3.0):
        self.seconds, self.max_gap = seconds, max_gap
        self.reset()

    def reset(self):
        self.started = self.last_at = self.sequence = None
        self.count = 0

    def observe(self, detected, sequence, now):
        if sequence == self.sequence:
            return None  # Repeated HTTP frames must never count as new evidence.
        if self.last_at is not None and not 0 <= now-self.last_at < self.max_gap:
            self.reset()
        self.sequence, self.last_at = sequence, now
        if not detected:
            self.started, self.count = None, 0
            return "green"
        if self.started is None:
            self.started = now
        self.count += 1
        return "red" if self.count >= 3 and now-self.started >= self.seconds else "gray"


class VisionDetector:
    def __init__(self, camera, callback, config=None, clock=time.monotonic):
        self.camera, self.callback, self.clock = camera, callback, clock
        self.config_error = None
        try:
            self.config = config or VisionSettings.from_env()
        except (ValueError, TypeError):
            self.config, self.config_error = VisionSettings(), "视觉配置有误，请检查 runtime.env"
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = None
        self.gate = ConfirmationGate(self.config.confirm_seconds, self.config.stale_seconds)
        self.jpeg = None
        self.result_at = None
        self.sequence = None
        self.completed_at = deque(maxlen=8)
        self.result = {"state": "error" if self.config_error else "starting" if self.config.enabled else "pending",
                       "detail": self.config_error or ("正在加载明火模型" if self.config.enabled else "尚未运行视觉检测，当前无法判断明火状态。"),
                       "model": "YOLOv8n Fire / 320", "confidence": None, "fps": None, "detected": False}

    def snapshot(self):
        with self.lock:
            result = dict(self.result, enabled=self.config.enabled, threshold=self.config.confidence,
                          confirm_seconds=self.config.confirm_seconds)
            if self.result_at is not None and self.clock()-self.result_at >= self.config.stale_seconds:
                result.update(state="offline", detail="视觉结果已过期，正在等待有效画面", confidence=None, fps=None, detected=False)
        if result["state"] in {"ready", "candidate", "alarm"} and self.camera.frame() is None:
            result.update(state="offline", detail="摄像头画面中断，当前无法判断明火状态", confidence=None, fps=None, detected=False)
        return result

    def frame(self):
        if self.camera.frame() is None:
            return None
        with self.lock:
            if self.jpeg is None or self.result_at is None or self.clock()-self.result_at >= self.config.stale_seconds:
                return None
            return self.jpeg, self.sequence

    def _unavailable(self, state, detail):
        with self.lock:
            changed = self.result["state"] != state
            self.result.update(state=state, detail=detail, confidence=None, fps=None, detected=False)
            self.jpeg, self.result_at = None, None
        self.gate.reset()
        self.completed_at.clear()
        if changed:
            self.callback({"level": "gray", "reason": detail}, None)

    def start(self):
        if not self.config.enabled or self.config_error or self.thread is not None:
            return
        self.thread = threading.Thread(target=self._loop, name="flame-inference", daemon=True)
        self.thread.start()

    def _loop(self):
        try:
            model = FireModel(self.config.confidence)
        except Exception as exc:
            LOGGER.exception("明火模型加载失败")
            self._unavailable("error", "模型加载失败，请检查依赖与模型文件（" + type(exc).__name__ + "）")
            return
        last_sequence = None
        while not self.stopping.is_set():
            started = self.clock()
            frame = self.camera.frame()
            if frame is None:
                self._unavailable("offline", "等待摄像头有效画面，当前无法判断明火状态")
            elif frame[1] != last_sequence:
                jpeg, sequence = frame
                frame_age = self.camera.snapshot().get("frame_age_seconds") or 0.
                last_sequence = sequence
                try:
                    detections, annotated = model.infer(jpeg)
                    elapsed = self.clock()-started
                    if self.stopping.is_set():
                        return
                    if elapsed + frame_age >= self.config.stale_seconds or self.camera.frame() is None:
                        self._unavailable("offline", "画面或推理结果已过期，等待下一帧")
                    else:
                        confidence = max((item["confidence"] for item in detections), default=0.)
                        level = self.gate.observe(bool(detections), sequence, self.clock())
                        state = "alarm" if level == "red" else "candidate" if detections else "ready"
                        reason = (f"连续检测到明火，置信度 {confidence:.0%}" if level == "red" else
                                  "发现疑似明火，正在连续确认" if detections else "当前画面未检测到明火")
                        observation = {"level": level, "reason": reason, "confidence": confidence,
                                       "model": model.name, "boxes": detections, "sequence": sequence,
                                       "threshold": self.config.confidence}
                        self.completed_at.append(self.clock())
                        span = self.completed_at[-1]-self.completed_at[0]
                        fps = round((len(self.completed_at)-1)/span, 2) if span > 0 else None
                        with self.lock:
                            self.jpeg, self.sequence, self.result_at = annotated, sequence, self.clock()
                            self.result.update(state=state, detail=reason, confidence=confidence,
                                               fps=fps,
                                               inference_ms=round(elapsed*1000), detected=bool(detections))
                        self.callback(observation, annotated)
                except Exception:
                    LOGGER.exception("视觉推理失败")
                    self._unavailable("error", "视觉推理失败，正在重试")
            self.stopping.wait(max(.05, 1/self.config.fps-(self.clock()-started)))

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=5)
        with self.lock:
            self.jpeg, self.result_at = None, None
            if self.config.enabled:
                self.result.update(state="offline", detail="视觉检测已停止", confidence=None, fps=None)
