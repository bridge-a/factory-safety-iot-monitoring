"""Real browser checks of integrated vision UI using labelled synthetic evidence."""
import io
import logging
from pathlib import Path
import sys
import threading
import time
import uuid

from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from camera_capture import CameraCapture, CameraSettings
from dashboard import create_app
from notification_core import MailSettings
from runtime import MonitorRuntime


def main():
    out = ROOT / "artifacts/vision-integration"
    out.mkdir(parents=True, exist_ok=True)
    camera = CameraCapture(CameraSettings(enabled=True))
    runtime = MonitorRuntime(out / ("data-"+uuid.uuid4().hex), mode="demo", mail=MailSettings(), camera=camera)
    runtime.tick()
    source = Image.new("RGB", (1280, 720), "#243b33")
    draw = ImageDraw.Draw(source)
    draw.text((100, 100), "TEST FRAME / NOT A REAL DETECTION", fill="white", font_size=32)
    draw.rectangle((450, 200, 720, 550), outline="#df5545", width=4)
    buffer = io.BytesIO()
    source.save(buffer, "JPEG")
    jpeg = buffer.getvalue()
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, create_app(runtime), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stop = threading.Event()
    def producer():
        while not stop.wait(.2):
            camera._publish(jpeg)
            runtime.tick()
    thread = threading.Thread(target=producer, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width":1366,"height":900})
            page.on("pageerror", lambda error: errors.append(str(error)))
            for path in ("/", "/history", "/alerts", "/vision", "/devices", "/simulation"):
                page.goto(url+path)
                expect(page.locator("#modeLabel")).to_have_text("全模拟演示")
            runtime.vision.config = type(runtime.vision.config)(enabled=True)
            with runtime.vision.lock:
                runtime.vision.result.update(state="ready", detail="当前画面未检测到明火", confidence=0., fps=2.)
            page.goto(url+"/vision")
            expect(page.locator("#visionStateLabel")).to_have_text("检测运行中")
            expect(page.locator("#visionFps")).to_have_text("2.0")
            expect(page.locator("#visionModelState")).to_have_text("YOLOv8n Fire / 320")
            runtime.observe_vision(dict(level="red", reason="测试夹具：连续检测到明火", confidence=.88,
                                       model="test fixture", boxes=[], sequence=1, threshold=.55), jpeg)
            with runtime.vision.lock:
                runtime.vision.result.update(state="alarm", detail="测试夹具：连续检测到明火", confidence=.88)
            page.locator("#refreshVision").click()
            expect(page.locator("#visionStateLabel")).to_have_text("检测到明火")
            expect(page.locator("#visionConfidence")).to_have_text("88")
            expect(page.locator(".vision-event-thumbnail")).to_be_visible()
            page.locator(".vision-event-detail").click()
            expect(page.locator("#eventDialog")).to_be_visible()
            expect(page.locator(".vision-event-image")).to_be_visible()
            page.keyboard.press("Escape")
            for width, height in ((1920,1080),(1366,900),(768,1024),(390,844),(320,760)):
                page.set_viewport_size({"width":width,"height":height})
                page.goto(url+"/vision")
                expect(page.locator("#visionStateLabel")).to_have_text("检测到明火")
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
                if width in (1366,390):
                    page.screenshot(path=str(out/f"vision-{width}.png"), full_page=True)
            page.set_viewport_size({"width":1366,"height":900})
            page.goto(url)
            expect(page.locator("#riskLabel")).to_have_text("红色报警")
            expect(page.locator("#riskSource")).to_have_text("明火检测")
            expect(page.locator("#overviewVisionConfidence")).to_have_text("88%")
            page.goto(url+"/alerts?source=vision")
            page.locator("button", has_text="详情").first.click()
            expect(page.locator(".vision-event-image")).to_be_visible()
            page.keyboard.press("Escape")
            with runtime.vision.lock:
                runtime.vision.result.update(state="offline", detail="摄像头中断", confidence=None, fps=None)
            page.goto(url+"/vision")
            expect(page.locator("#visionStateLabel")).to_have_text("明火报警 · 待确认恢复")
            expect(page.locator("#visionFps")).to_have_text("—")
            assert not errors, errors
            browser.close()
        print("PASS: six routes; vision readiness/alarm/offline; evidence in both details; risk linkage; five viewport widths; no JS errors")
    finally:
        stop.set()
        thread.join(3)
        server.shutdown()


if __name__ == "__main__":
    main()
