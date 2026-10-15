"""Real HTTP/browser preview checks using an explicitly labelled test JPEG only."""
import base64
import json
import logging
from pathlib import Path
import sys
import threading
import time
import uuid

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from camera_capture import CameraCapture, CameraSettings
from dashboard import create_app
from notification_core import MailSettings
from runtime import MonitorRuntime


def main():
    out = ROOT / 'artifacts/camera-check'
    out.mkdir(parents=True, exist_ok=True)
    camera = CameraCapture(CameraSettings(enabled=True))
    runtime = MonitorRuntime(out / ('data-' + uuid.uuid4().hex), mode='demo', mail=MailSettings(), camera=camera)
    runtime.tick()
    app = create_app(runtime)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}'
    stop, available = threading.Event(), threading.Event()
    errors, layouts, checks = [], [], []
    producer = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={'width':1366,'height':768})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url+'/vision')
            jpeg_url = page.evaluate('''() => {
                const c=document.createElement('canvas');c.width=1280;c.height=720;
                const g=c.getContext('2d');g.fillStyle='#233d32';g.fillRect(0,0,1280,720);
                g.strokeStyle='#719080';g.lineWidth=2;
                for(let x=0;x<=1280;x+=80){g.beginPath();g.moveTo(x,0);g.lineTo(x,720);g.stroke();}
                for(let y=0;y<=720;y+=80){g.beginPath();g.moveTo(0,y);g.lineTo(1280,y);g.stroke();}
                g.fillStyle='#ffffff';g.font='bold 54px sans-serif';g.fillText('TEST FRAME - NO CAMERA',180,340);
                g.font='28px sans-serif';g.fillText('1280 x 720 / browser verification fixture',180,400);
                return c.toDataURL('image/jpeg',.8);
            }''')
            jpeg = base64.b64decode(jpeg_url.split(',',1)[1])

            def publish():
                last_tick = 0
                while not stop.wait(.1):
                    if available.is_set():
                        camera._publish(jpeg)
                    if time.monotonic() - last_tick > 2:
                        runtime.tick()
                        last_tick = time.monotonic()

            producer = threading.Thread(target=publish, daemon=True)
            producer.start()
            camera._state('disabled','摄像头采集未启用')
            page.locator('#refreshVision').click()
            expect(page.locator('#visionCameraState')).to_have_text('未启用')
            expect(page.locator('[data-camera-image]')).to_be_hidden()
            available.set()
            expect(page.locator('[data-camera-stage]')).to_have_attribute('data-camera-live','true')
            expect(page.locator('#visionModelState')).to_have_text('待部署')
            expect(page.locator('#visionStateLabel')).to_have_text('检测未启用')
            checks.append('disabled to live; video does not claim model detection')

            for width,height in [(1920,1080),(1366,768),(768,1024),(390,844),(320,760)]:
                page.set_viewport_size({'width':width,'height':height})
                for name,path in [('overview','/'),('vision','/vision')]:
                    page.goto(url+path)
                    expect(page.locator('[data-camera-stage]')).to_have_attribute('data-camera-live','true')
                    expect(page.locator('[data-camera-image]')).to_be_visible()
                    layout=page.evaluate('''() => {
                        const img=document.querySelector('[data-camera-image]');
                        return {width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
                            fit:getComputedStyle(img).objectFit,natural:[img.naturalWidth,img.naturalHeight]};
                    }''')
                    assert layout['scrollWidth'] <= width and layout['fit']=='contain',layout
                    assert layout['natural']==[1280,720],layout
                    layouts.append({'page':name,**layout})
                    if width in (1366,390):
                        page.screenshot(path=str(out/f'{name}-{width}.png'),full_page=True)
            checks.append('two video pages at five widths; full 16:9 image and no horizontal overflow')

            page.goto(url+'/vision')
            expect(page.locator('[data-camera-stage]')).to_have_attribute('data-camera-live','true')
            page.route('**/api/camera/frame',lambda route: route.fulfill(status=503,json={'error':'test disconnect'}))
            expect(page.locator('[data-camera-image]')).to_be_hidden(timeout=4000)
            page.unroute('**/api/camera/frame')
            expect(page.locator('[data-camera-image]')).to_be_visible(timeout=5000)
            page.route('**/api/camera/frame',lambda route: route.fulfill(content_type='image/jpeg',body=b'invalid'))
            expect(page.locator('[data-camera-image]')).to_be_hidden(timeout=4000)
            page.unroute('**/api/camera/frame')
            expect(page.locator('[data-camera-image]')).to_be_visible(timeout=5000)
            checks.append('HTTP and corrupt-image failures clear old image and retry successfully')

            available.clear()
            time.sleep(.2)
            camera._state('reconnecting','未找到摄像头，请检查 USB 连接')
            page.locator('#refreshVision').click()
            expect(page.locator('#visionCameraState')).to_have_text('重连中')
            expect(page.locator('[data-camera-image]')).to_be_hidden()
            available.set()
            expect(page.locator('[data-camera-image]')).to_be_visible(timeout=5000)
            page.route('**/api/status',lambda route:route.fulfill(status=503,json={'error':'test offline'}))
            page.locator('#refreshVision').click()
            expect(page.locator('#visionCameraState')).to_have_text('状态未知')
            expect(page.locator('[data-camera-image]')).to_be_hidden()
            page.unroute('**/api/status')
            page.locator('#refreshVision').click()
            expect(page.locator('[data-camera-image]')).to_be_visible(timeout=5000)
            checks.append('device unplug and backend failure clear stale video; both recover')

            second = browser.new_page()
            second.on('pageerror',lambda error:errors.append(str(error)))
            second.goto(url+'/')
            expect(second.locator('[data-camera-image]')).to_be_visible()
            expect(page.locator('[data-camera-image]')).to_be_visible()
            assert camera.thread is None and camera.sequence > 1
            second.goto(url+'/devices')
            expect(second.locator('#deviceVisionState')).to_have_text('已接入')
            page.close(); second.close()
            sequence = camera.sequence
            time.sleep(.25)
            assert camera.sequence > sequence
            checks.append('two viewers share frames; device status updates; capture independent from open pages')
            assert not errors,errors
            browser.close()
        report={'status':'passed','checks':checks,'layouts':layouts,'javascript_errors':errors,
                'physical_camera_used':False,'real_email_sent':False}
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k!='layouts'},ensure_ascii=False,indent=2))
    finally:
        stop.set()
        if producer: producer.join(timeout=3)
        runtime.stop()
        server.shutdown()


if __name__=='__main__':
    main()
