"""Vision workspace checks with isolated event fixtures; no camera, model or mail."""
import json
import logging
import sys
import threading
import time
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard import create_app
from notification_core import MailSettings
from runtime import MonitorRuntime


def main():
    out = ROOT / 'artifacts/vision-check'
    out.mkdir(parents=True, exist_ok=True)
    runtime = MonitorRuntime(out / ('data-' + uuid.uuid4().hex), mode='demo', mail=MailSettings())
    base = runtime.tick()
    app = create_app(runtime, start_runtime=True)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}'
    errors, checks, layouts = [], [], []

    def seed(index, source='vision', reason='模拟测试：明火检测事件'):
        data = dict(base, device=f'vision-fixture-{index}', event_source=source,
                    mode='demo', is_demo=True, metric='flame')
        evaluation = {'level':'red', 'reason':reason, 'abnormal':[('flame', 0.86, 'red')]}
        return runtime.alerts.observe(data, evaluation, now=time.time()-90+index)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={'width':1366, 'height':768}, timezone_id='Asia/Shanghai')
            page.on('pageerror', lambda error: errors.append(str(error)))
            for width, height in [(1366,768),(1920,1080),(768,1024),(390,844),(320,760)]:
                page.set_viewport_size({'width':width, 'height':height})
                page.goto(url+'/vision')
                expect(page.locator('#visionStatus')).to_have_attribute('data-state', 'pending')
                expect(page.locator('#visionEvents')).to_have_attribute('data-state', 'empty')
                expect(page.locator('#visionStateDescription')).to_contain_text('无法判断明火状态')
                expect(page.locator('#riskIndicatorText')).to_have_text('正常')
                expect(page.locator('nav [aria-current="page"]')).to_have_text('明火检测')
                expect(page.locator('#visionEventSummary')).to_have_text('全部模式 · 0 条记录')
                layout = page.evaluate('''() => ({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
                    videoTop:document.querySelector('#cameraScreen').getBoundingClientRect().top,
                    videoBottom:document.querySelector('#cameraScreen').getBoundingClientRect().bottom,
                    eventsTop:document.querySelector('#visionEvents').getBoundingClientRect().top})''')
                assert layout['scrollWidth'] <= width, layout
                if width >= 1366:
                    assert layout['videoTop'] < 250 and layout['videoBottom'] < height, layout
                layouts.append(layout)
                page.screenshot(path=str(out/f'vision-{width}.png'), full_page=True)
            checks.append('five viewport widths; honest pending state, empty records and independent environment risk')

            page.set_viewport_size({'width':1366, 'height':768})
            page.goto(url+'/vision')
            expect(page.locator('#visionEvents')).to_have_attribute('data-state', 'empty')
            with page.expect_request('**/api/alerts?*') as request:
                page.locator('#refreshVision').click()
            assert 'source=vision' in request.value.url
            expect(page.locator('#refreshVision')).to_be_enabled()
            expect(page.locator('#visionUpdated')).to_contain_text('更新于')
            checks.append('refresh requests current status and vision-filtered records')

            page.route('**/api/alerts?*', lambda route: route.fulfill(status=503, json={'error':'temporary failure'}))
            page.locator('#refreshVision').click()
            expect(page.locator('#visionEvents')).to_have_attribute('data-state', 'error')
            expect(page.locator('#visionEventSummary')).to_have_text('记录读取失败')
            expect(page.locator('#visionEventsStateTitle')).to_have_text('明火事件读取失败')
            page.unroute('**/api/alerts?*')
            page.locator('#refreshVision').click()
            expect(page.locator('#visionEvents')).to_have_attribute('data-state', 'empty')
            checks.append('query failure is distinct from no events and can recover with refresh')

            page.route('**/api/status', lambda route: route.fulfill(status=503, json={'error':'offline'}))
            page.locator('#refreshVision').click()
            expect(page.locator('#visionStatus')).to_have_attribute('data-state', 'unknown')
            expect(page.locator('#visionCameraState')).to_have_text('未知')
            expect(page.locator('#riskIndicatorText')).to_have_text('状态未知')
            expect(page.locator('#visionUpdated')).to_have_text('状态读取失败')
            page.screenshot(path=str(out/'vision-offline.png'), full_page=True)
            page.reload()
            expect(page.locator('#visionEvents')).to_have_attribute('data-state', 'error')
            expect(page.locator('#visionEvents')).to_have_attribute('aria-busy', 'false')
            page.unroute('**/api/status')
            page.locator('#refreshVision').click()
            expect(page.locator('#visionStatus')).to_have_attribute('data-state', 'pending')
            expect(page.locator('#visionEvents')).to_have_attribute('data-state', 'empty')
            checks.append('backend loss clears stale status; initial offline load does not stay loading; reconnect recovers')

            seed(0, source='smoke', reason='烟雾测试事件，不应出现在明火记录')
            for index in range(1,6):
                seed(index)
            # Stored reason text must remain literal rather than becoming HTML.
            seed(6, reason='模拟测试：<img src=x onerror=alert(1)>')
            page.reload()
            expect(page.locator('#visionEventList>li')).to_have_count(4)
            expect(page.locator('#visionEventSummary')).to_have_text('全部模式 · 最近 4 条 / 共 6 条')
            expect(page.locator('#visionEventList')).not_to_contain_text('烟雾测试事件')
            expect(page.locator('#visionEventList')).to_contain_text('模拟数据')
            expect(page.locator('#visionEventList img')).to_have_count(0)
            expect(page.locator('#visionEventList')).to_contain_text('<img src=x onerror=alert(1)>')
            expect(page.locator('#visionEvidenceNote')).to_be_visible()
            expect(page.locator('#visionStatus')).to_have_attribute('data-state', 'pending')
            page.screenshot(path=str(out/'vision-fixture-events.png'), full_page=True)
            page.set_viewport_size({'width':390, 'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(out/'vision-fixture-events-mobile.png'), full_page=True)
            checks.append('only vision records, four newest of six, honest simulation labels, safe text and no fake screenshots')

            page.get_by_role('link', name='全部记录').click()
            expect(page.locator('#alertSource')).to_have_value('vision')
            expect(page.locator('#eventTotal')).to_have_text('6')
            checks.append('all-records link opens existing alerts with the vision filter applied')
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        runtime.stop()
    report = {'checks':checks, 'layouts':layouts, 'errors':errors}
    (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
