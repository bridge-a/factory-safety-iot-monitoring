"""Camera checks without a physical camera, MQTT or SMTP."""
import io
import subprocess
import threading
import time

import pytest

from camera_capture import CameraCapture, CameraSettings, JpegFrames, MAX_FRAME_BYTES
from dashboard import create_app
from notification_core import MailSettings
from runtime import MonitorRuntime

JPEG = b"\xff\xd8fixture\xff\xd9"


def test_jpeg_chunks_and_memory_bound():
    parser = JpegFrames()
    assert parser.feed(b"junk\xff") == []
    assert parser.feed(b"\xd8fixture\xff") == []
    assert parser.feed(b"\xd9" + JPEG) == [JPEG, JPEG]
    assert parser.feed(b"\xff\xd8" + b"x" * MAX_FRAME_BYTES) == []
    assert len(parser.buffer) <= 1
    assert parser.feed(JPEG) == [JPEG]


def test_disabled_and_config_errors(monkeypatch):
    for name in ("FACTORY_CAMERA_ENABLED", "FACTORY_CAMERA_DEVICE", "FACTORY_CAMERA_WIDTH",
                 "FACTORY_CAMERA_HEIGHT", "FACTORY_CAMERA_FPS", "FACTORY_CAMERA_ROTATION"):
        monkeypatch.delenv(name, raising=False)
    camera = CameraCapture()
    camera.start()
    assert camera.snapshot()["state"] == "disabled"
    assert camera.thread is None and camera.frame() is None
    monkeypatch.setenv("FACTORY_CAMERA_WIDTH", "invalid")
    invalid = CameraCapture()
    invalid.start()
    assert invalid.snapshot()["state"] == "error"
    assert invalid.thread is None
    monkeypatch.setenv("FACTORY_CAMERA_WIDTH", "1280")
    monkeypatch.setenv("FACTORY_CAMERA_DEVICE", "https://example.com/video")
    with pytest.raises(ValueError):
        CameraSettings.from_env()


def test_latest_only_stale_and_api_independent_from_model(tmp_path):
    now = [0.0]
    camera = CameraCapture(CameraSettings(enabled=True), clock=lambda: now[0])
    runtime = MonitorRuntime(tmp_path, mode="demo", mail=MailSettings(), camera=camera)
    runtime.tick()
    client = create_app(runtime).test_client()
    assert client.get('/api/camera/frame').status_code == 503
    camera._publish(JPEG)
    camera._publish(JPEG + b"last")
    first = client.get('/api/camera/frame')
    second = client.get('/api/camera/frame')
    assert first.data == second.data == JPEG + b"last"
    assert first.headers['Content-Type'] == 'image/jpeg'
    assert first.headers['Cache-Control'] == 'no-store'
    assert first.headers['X-Camera-Sequence'] == second.headers['X-Camera-Sequence'] == '2'
    assert camera.thread is None  # HTTP requests never open another device.
    status = client.get('/api/status').json
    assert status['camera']['state'] == 'ready'
    assert status['vision']['state'] == 'pending'
    now[0] = 3
    assert client.get('/api/camera/frame').status_code == 503
    assert client.get('/api/status').json['camera']['state'] == 'reconnecting'
    camera._publish(JPEG)
    assert client.get('/api/camera/frame').status_code == 200
    camera.stop()
    assert client.get('/api/camera/frame').status_code == 503


def test_reader_and_command_keep_mjpeg_compressed():
    camera = CameraCapture(CameraSettings(enabled=True))
    camera._read_frames(io.BytesIO(JPEG * 3))
    assert camera.frame() == (JPEG, 3)
    command = camera.config.command('/usr/bin/ffmpeg')
    assert command[command.index('-c:v') + 1] == 'copy'
    assert command[command.index('-input_format') + 1] == 'mjpeg'
    assert command[command.index('-bsf:v') + 1] == 'mjpeg2jpeg'
    assert command[command.index('-video_size') + 1] == '1280x720'
    assert command[-1] == 'pipe:1'


def test_upside_down_camera_encodes_shared_rotated_frames(monkeypatch):
    monkeypatch.setenv('FACTORY_CAMERA_ROTATION', '180')
    config = CameraSettings.from_env()
    command = config.command('/usr/bin/ffmpeg')
    assert command[command.index('-vf') + 1] == 'fps=10,hflip,vflip'
    assert command[command.index('-c:v') + 1] == 'mjpeg'
    assert 'copy' not in command and '-bsf:v' not in command
    assert CameraCapture(config).snapshot()['rotation'] == 180
    # A lower input rate must not be increased by the rotation path.
    monkeypatch.setenv('FACTORY_CAMERA_FPS', '5')
    command = CameraSettings.from_env().command('/usr/bin/ffmpeg')
    assert command[command.index('-vf') + 1] == 'fps=5,hflip,vflip'
    monkeypatch.setenv('FACTORY_CAMERA_ROTATION', '0')
    command = CameraSettings.from_env().command('/usr/bin/ffmpeg')
    assert '-vf' not in command and command[command.index('-c:v') + 1] == 'copy'
    monkeypatch.setenv('FACTORY_CAMERA_ROTATION', '90')
    with pytest.raises(ValueError):
        CameraSettings.from_env()


def test_worker_recovers_after_device_missing(monkeypatch):
    import camera_capture as module
    camera = CameraCapture(CameraSettings(enabled=True, retry_seconds=.01))
    calls = []
    monkeypatch.setattr(module.shutil, 'which', lambda name: '/usr/bin/ffmpeg')
    monkeypatch.setattr(module.Path, 'exists', lambda path: bool(calls))
    original_wait = camera.stopping.wait

    def wait(seconds):
        calls.append('wait')
        return original_wait(min(seconds, .01))

    def capture(executable):
        camera._publish(JPEG)
        calls.append(camera.frame())
        camera.stopping.set()

    monkeypatch.setattr(camera.stopping, 'wait', wait)
    monkeypatch.setattr(camera, '_capture_once', capture)
    thread = threading.Thread(target=camera._run)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert (JPEG, 1) in calls
    assert camera.snapshot()['state'] == 'stopped'
    assert camera.frame() is None


def test_process_is_terminated_when_no_frames(monkeypatch):
    import camera_capture as module

    class Process:
        def __init__(self):
            self.stdout = io.BytesIO(b'')
            self.stderr = io.BytesIO(b'')
            self.code = None
        def poll(self):
            return self.code
        def terminate(self):
            self.code = 0
        def wait(self, timeout):
            return self.code

    process = Process()
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: process)
    camera = CameraCapture(CameraSettings(enabled=True, startup_seconds=.01))
    started = time.monotonic()
    camera._capture_once('ffmpeg')
    assert time.monotonic() - started < 2
    assert process.code == 0 and camera.process is None
    assert process.stdout.closed and process.stderr.closed


def test_hung_process_is_killed():
    class Process:
        killed = False
        def poll(self):
            return None
        def terminate(self):
            pass
        def kill(self):
            self.killed = True
        def wait(self, timeout):
            if not self.killed:
                raise subprocess.TimeoutExpired('ffmpeg', timeout)
            return 0

    process = Process()
    CameraCapture._terminate(process)
    assert process.killed
