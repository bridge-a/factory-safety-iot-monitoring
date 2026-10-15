"""No MQTT/SMTP traffic or physical camera needed for vision contract tests."""
import io
import time

import pytest

from camera_capture import CameraCapture, CameraSettings
from dashboard import create_app
from notification_core import MailSettings, compose_mail
from runtime import MonitorRuntime
from vision_detector import ConfirmationGate, VisionDetector, VisionSettings


def test_confirmation_requires_distinct_frames_time_and_continuity():
    gate = ConfirmationGate(1.5)
    assert gate.observe(True, 1, 0) == "gray"
    assert gate.observe(True, 1, 2) is None
    assert gate.observe(True, 2, .5) == "gray"
    assert gate.observe(True, 3, 1) == "gray"
    assert gate.observe(True, 4, 1.5) == "red"
    assert gate.observe(False, 5, 2) == "green"
    assert gate.observe(True, 6, 3) == "gray"
    assert gate.observe(True, 7, 7) == "gray"  # Gap resets evidence.
    gate.reset()
    assert gate.observe(True, 8, 8) == "gray"


def test_decode_letterbox_class_selection_and_nms():
    np = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    from vision_detector import decode_fire, letterbox
    tensor, transform = letterbox(Image.new("RGB", (1280, 720), "red"))
    assert tensor.shape == (1, 3, 320, 320) and tensor.dtype == np.float32
    assert transform == (.25, 0, 70)
    assert tensor[0, 0, 100, 100] == 1 and tensor[0, 1, 100, 100] == 0
    output = np.zeros((1, 6, 2100), dtype=np.float32)
    output[0, :, 0] = [160, 160, 80, 60, .9, .1]
    output[0, :, 1] = [161, 161, 80, 60, .8, .1]  # duplicate box
    output[0, :, 2] = [30, 100, 20, 20, .6, .95]  # smoke-dominant
    output[0, :, 3] = [30, 100, 20, 20, .4, .1]  # below threshold
    output[0, :, 4] = [float("nan"), 100, 20, 20, .9, .1]
    boxes = decode_fire(output, transform, (1280, 720), .55)
    assert len(boxes) == 1
    assert boxes[0]["box"] == [480, 240, 800, 480]


def observation(level="red"):
    return dict(level=level, reason="连续检测到明火" if level == "red" else "当前画面未检测到明火",
                confidence=.85 if level == "red" else 0., model="test fire", boxes=[], sequence=1, threshold=.55)


def test_event_evidence_mail_and_missing_does_not_recover(tmp_path):
    now = [100.]
    runtime = MonitorRuntime(tmp_path, mode="demo", mail=MailSettings(enabled=True), clock=lambda: now[0])
    runtime.tick()
    runtime.observe_vision(observation(), b"test-evidence")
    runtime.observe_vision(observation(), b"duplicate")
    events = runtime.alerts.list_events()["events"]
    assert len(events) == 1 and events[0]["source"] == "vision"
    assert events[0]["is_demo"] is False and events[0]["mail_status"] == "pending"
    assert runtime.snapshot()["sample"]["level"] == "red"
    client = create_app(runtime).test_client()
    assert client.get(f"/api/vision/events/{events[0]['id']}/image").data == b"test-evidence"
    assert client.get("/api/vision/events/9999/image").status_code == 404
    assert client.get("/api/vision/events/../mail.ini/image").status_code == 404
    item = runtime.alerts.claim_mail(now[0])
    mail = compose_mail(item, MailSettings(username="sender@example.com", recipient="receiver@example.com"))
    assert "置信度：85%" in mail.get_content() and "模拟演示" not in mail["Subject"]
    for seconds in range(102, 116, 2):
        now[0] = seconds
        runtime.observe_vision(observation("green"), None)
    now[0] = 116
    runtime.observe_vision({"level": "gray", "reason": "camera offline"}, None)
    for seconds in range(130, 146, 2):
        now[0] = seconds
        runtime.observe_vision(observation("green"), None)
        assert runtime.snapshot()["vision"]["alarm_active"]
    now[0] = 146
    runtime.observe_vision(observation("green"), None)
    assert not runtime.snapshot()["vision"]["alarm_active"]
    assert len(runtime.alerts.list_events()["events"]) == 2


def test_visual_alarm_reaches_existing_control_channel(tmp_path):
    runtime = MonitorRuntime(tmp_path, mode="demo", mail=MailSettings(), clock=lambda: 100.)
    class Client:
        messages = []
        def publish(self, topic, payload, **options):
            self.messages.append((topic, payload))
    runtime.client = Client()
    runtime.mqtt_connected, runtime.received_at = True, 100.
    runtime.received = {"alarm_token": "token-100", "sound_light": {"effective_level": "green", "remote_active": False}}
    runtime.observe_vision(observation(), None)
    runtime.tick()
    assert runtime.client.messages[-1][1] == "token-100|red"
    assert runtime.sample["level"] == "green"  # Environment history remains environmental.
    assert runtime.snapshot()["sample"]["level"] == "red"


def test_worker_shared_frames_and_stale_results(monkeypatch):
    import vision_detector
    camera = CameraCapture(CameraSettings(enabled=True))
    calls, results = [], []
    class FakeModel:
        name = "test"
        def __init__(self, confidence):
            pass
        def infer(self, jpeg):
            calls.append(jpeg)
            return [{"box": [0, 0, 10, 10], "confidence": .9}], b"annotated"
    monkeypatch.setattr(vision_detector, "FireModel", FakeModel)
    worker = VisionDetector(camera, lambda result, jpeg: results.append(result), VisionSettings(enabled=True, fps=5))
    camera._publish(b"frame1")
    worker.start()
    try:
        deadline = time.monotonic()+2
        while not calls and time.monotonic() < deadline:
            time.sleep(.01)
        time.sleep(.3)
        assert calls == [b"frame1"]
        assert worker.frame() == (b"annotated", 1)
        assert worker.snapshot()["state"] == "candidate"
        camera._state("reconnecting", "disconnected")
        assert worker.frame() is None and worker.snapshot()["state"] == "offline"
        time.sleep(.3)
        assert results[-1]["level"] == "gray"
    finally:
        worker.stop()
    assert camera.thread is None  # Vision never opens another capture process.


def test_model_load_failure_is_nonfatal(monkeypatch):
    import vision_detector
    def fail(*args):
        raise ValueError("missing weights")
    monkeypatch.setattr(vision_detector, "FireModel", fail)
    worker = VisionDetector(CameraCapture(CameraSettings()), lambda *args: None, VisionSettings(enabled=True))
    worker.start()
    worker.thread.join(2)
    assert worker.snapshot()["state"] == "error"
    assert worker.frame() is None


def test_enable_preserves_camera_mail_and_custom_settings(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / "deploy/raspberry-pi/setup_vision.py"
    spec = importlib.util.spec_from_file_location("setup_vision_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    path = tmp_path / "deploy/raspberry-pi/runtime.env"
    path.parent.mkdir(parents=True)
    original = ("FACTORY_CAMERA_ROTATION=180\r\nFACTORY_MQTT_HOST=127.0.0.1\r\n"
                "FACTORY_VISION_CONFIDENCE=0.7\r\nFACTORY_VISION_ENABLED=0\r\n"
                "FACTORY_MAIL_CONFIG='/home/pi/factory_monitoring/mail.ini'\r\n").encode()
    path.write_bytes(original)
    module.enable()
    result = path.read_text()
    assert "FACTORY_CAMERA_ROTATION=180" in result and "FACTORY_VISION_CONFIDENCE=0.7" in result
    assert "FACTORY_MQTT_HOST=127.0.0.1" in result and "FACTORY_MAIL_CONFIG=" in result
    assert result.count("FACTORY_VISION_ENABLED=") == 1 and "FACTORY_VISION_ENABLED=1" in result
    backups = list(path.parent.glob("runtime.env.bak-vision-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == original


def test_local_pinned_model_inference_if_available():
    pytest.importorskip("onnxruntime")
    Image = pytest.importorskip("PIL.Image")
    from vision_detector import FireModel, MODEL_DIR
    if not (MODEL_DIR / "fire_yolov8n.onnx").is_file():
        pytest.skip("optional model weights not downloaded")
    buffer = io.BytesIO()
    Image.new("RGB", (1280, 720), (100, 100, 100)).save(buffer, "JPEG")
    boxes, jpeg = FireModel().infer(buffer.getvalue())
    assert boxes == []
    assert Image.open(io.BytesIO(jpeg)).size == (1280, 720)
