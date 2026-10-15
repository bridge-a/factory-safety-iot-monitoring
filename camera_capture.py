"""One optional V4L2/MJPEG capture process; keep only the newest JPEG in RAM."""
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

LOGGER = logging.getLogger("factory-camera")
MAX_FRAME_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class CameraSettings:
    enabled: bool = False
    device: str = "/dev/video0"
    width: int = 1280
    height: int = 720
    fps: int = 30
    stale_seconds: float = 3.0
    startup_seconds: float = 12.0
    retry_seconds: float = 5.0

    @classmethod
    def from_env(cls):
        enabled = os.environ.get("FACTORY_CAMERA_ENABLED", "0").lower()
        if enabled not in {"0", "1", "false", "true"}:
            raise ValueError("FACTORY_CAMERA_ENABLED 应为 0 或 1")
        result = cls(enabled=enabled in {"1", "true"},
                     device=os.environ.get("FACTORY_CAMERA_DEVICE", "/dev/video0"),
                     width=int(os.environ.get("FACTORY_CAMERA_WIDTH", "1280")),
                     height=int(os.environ.get("FACTORY_CAMERA_HEIGHT", "720")),
                     fps=int(os.environ.get("FACTORY_CAMERA_FPS", "30")))
        if not result.device.startswith("/dev/"):
            raise ValueError("摄像头路径应位于 /dev/ 下")
        if not (160 <= result.width <= 1920 and 120 <= result.height <= 1080 and 1 <= result.fps <= 60):
            raise ValueError("摄像头分辨率或帧率配置超出支持范围")
        return result

    def command(self, executable):
        # MJPEG stays compressed: no PyTorch/OpenCV or per-viewer capture process.
        return [executable, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "v4l2", "-input_format", "mjpeg", "-video_size", f"{self.width}x{self.height}",
                "-framerate", str(self.fps), "-i", self.device, "-map", "0:v:0",
                "-an", "-c:v", "copy", "-bsf:v", "mjpeg2jpeg", "-f", "image2pipe", "pipe:1"]


class JpegFrames:
    """Extract complete JPEGs across arbitrary pipe reads with bounded memory."""
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk):
        self.buffer.extend(chunk)
        frames = []
        while True:
            start = self.buffer.find(b"\xff\xd8")
            if start < 0:
                self.buffer[:] = self.buffer[-1:]
                break
            if start:
                del self.buffer[:start]
            end = self.buffer.find(b"\xff\xd9", 2)
            if end < 0:
                if len(self.buffer) > MAX_FRAME_BYTES:
                    self.buffer.clear()
                break
            if end + 2 <= MAX_FRAME_BYTES:
                frames.append(bytes(self.buffer[:end + 2]))
            del self.buffer[:end + 2]
        return frames


class CameraCapture:
    def __init__(self, config=None, clock=time.monotonic):
        self.config_error = None
        try:
            self.config = config or CameraSettings.from_env()
        except (ValueError, TypeError):
            self.config = CameraSettings()
            self.config_error = "摄像头配置有误，请检查 runtime.env"
        self.clock = clock
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = None
        self.process = None
        self.jpeg = None
        self.frame_at = None
        self.sequence = 0
        self.state = "error" if self.config_error else "starting" if self.config.enabled else "disabled"
        self.detail = self.config_error or ("等待启动采集" if self.config.enabled else "摄像头采集未启用")

    def _state(self, state, detail, clear=True):
        with self.lock:
            self.state, self.detail = state, detail
            if clear:
                self.jpeg, self.frame_at = None, None

    def _publish(self, jpeg):
        with self.lock:
            self.jpeg, self.frame_at = jpeg, self.clock()
            self.sequence += 1
            self.state, self.detail = "ready", "实时画面已接入"

    def frame(self):
        with self.lock:
            if self.state != "ready" or self.frame_at is None or self.clock() - self.frame_at >= self.config.stale_seconds:
                return None
            return self.jpeg, self.sequence

    def snapshot(self):
        with self.lock:
            age = None if self.frame_at is None else max(0, self.clock() - self.frame_at)
            stale = self.state == "ready" and (age is None or age >= self.config.stale_seconds)
            return {"state": "reconnecting" if stale else self.state,
                    "detail": "画面中断，正在重新连接" if stale else self.detail,
                    "enabled": self.config.enabled, "device": self.config.device,
                    "resolution": f"{self.config.width} × {self.config.height}",
                    "frame_age_seconds": round(age, 2) if age is not None else None,
                    "sequence": self.sequence, "preview_url": "/api/camera/frame"}

    def _read_frames(self, stream):
        parser = JpegFrames()
        try:
            while not self.stopping.is_set():
                chunk = stream.read(65536)
                if not chunk:
                    return
                for jpeg in parser.feed(chunk):
                    self._publish(jpeg)
        except (OSError, ValueError):
            return

    @staticmethod
    def _read_errors(stream):
        # Drain stderr so FFmpeg cannot stall on a full pipe; keep diagnostics local.
        try:
            while True:
                line = stream.readline(2048)
                if not line:
                    return
                LOGGER.warning("FFmpeg: %s", line.decode("utf-8", errors="replace").strip())
        except (OSError, ValueError):
            return

    @staticmethod
    def _terminate(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def _capture_once(self, executable):
        process = subprocess.Popen(self.config.command(executable), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, bufsize=0)
        with self.lock:
            self.process = process
        readers = [threading.Thread(target=self._read_frames, args=(process.stdout,), daemon=True),
                   threading.Thread(target=self._read_errors, args=(process.stderr,), daemon=True)]
        for reader in readers:
            reader.start()
        started = self.clock()
        try:
            while not self.stopping.wait(.2) and process.poll() is None:
                with self.lock:
                    last = self.frame_at
                if (last is None and self.clock() - started > self.config.startup_seconds or
                        last is not None and self.clock() - last >= self.config.stale_seconds):
                    break
        finally:
            self._terminate(process)
            for reader in readers:
                reader.join(timeout=2)
            process.stdout.close()
            process.stderr.close()
            with self.lock:
                self.process = None

    def _run(self):
        while not self.stopping.is_set():
            executable = shutil.which("ffmpeg")
            if not executable:
                self._state("error", "未安装 FFmpeg，无法采集画面")
            elif not Path(self.config.device).exists():
                self._state("reconnecting", "未找到摄像头，请检查 USB 连接")
            else:
                self._state("starting", "正在连接摄像头")
                try:
                    self._capture_once(executable)
                    self._state("reconnecting", "采集中断，正在重连；请检查占用、权限与画面格式")
                except OSError:
                    LOGGER.exception("摄像头采集进程启动失败")
                    self._state("error", "采集启动失败，请检查 FFmpeg 与设备权限")
            if self.stopping.wait(self.config.retry_seconds):
                break
        self._state("stopped", "摄像头采集已停止")

    def start(self):
        with self.lock:
            if not self.config.enabled or self.config_error or self.thread is not None:
                return
            if os.name != "posix":
                self.state, self.detail = "error", "USB 采集请在树莓派 Linux 上运行"
                return
            self.thread = threading.Thread(target=self._run, name="factory-camera", daemon=True)
            self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(timeout=7)
        self._state("stopped", "摄像头采集已停止")
