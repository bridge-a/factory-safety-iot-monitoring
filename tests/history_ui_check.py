"""Historical-query UI regression with isolated fixtures; no external mail or hardware."""
import csv
import io
import json
import logging
import math
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
    out = ROOT / 'artifacts/history-check'
    out.mkdir(parents=True, exist_ok=True)
    runtime = MonitorRuntime(out / ('data-' + uuid.uuid4().hex), mode='demo', mail=MailSettings())
    base = runtime.tick()
    now = time.time()
    for i in range(900):
        age = 5400 - i * 6
        smoke = 380 + 75 * math.sin(i / 8) + 20 * math.sin(i / 3)
        if 180 < age < 260:
            smoke += 2100 * math.sin((age - 180) / 80 * math.pi)
        sample = dict(base, ts=now-age, temp_c=round(27+1.5*math.sin(i/34), 1),
                      rh=round(53+4*math.sin(i/60), 1), co2_ppm=round(760+110*math.sin(i/31)),
                      smoke_raw=round(smoke) if i % 71 else None)
        runtime.env.append(sample)
        runtime.env.append(dict(sample, mode='hybrid', co2_ppm=None,
                                sources={'temp_c':'mqtt', 'rh':'mqtt', 'co2_ppm':'missing', 'smoke_raw':'simulation'}))
    app = create_app(runtime, start_runtime=True)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}'
    errors, checks, layouts = [], [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={'width':1366, 'height':768}, timezone_id='Asia/Shanghai')
            page.on('pageerror', lambda error: errors.append(str(error)))
            for width, height in [(1366,768), (1920,1080), (390,844), (768,1024)]:
                page.set_viewport_size({'width':width, 'height':height})
                page.goto(url+'/history')
                expect(page.locator('#historyPanel')).to_have_attribute('data-state', 'ready')
                expect(page.locator('#riskIndicatorText')).to_have_text('正常')
                expect(page.locator('nav [aria-current="page"]')).to_have_text('历史趋势')
                layout = page.evaluate('''() => ({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
                    chartTop:document.querySelector('#historyChart').getBoundingClientRect().top,
                    chartBottom:document.querySelector('#historyChart').getBoundingClientRect().bottom})''')
                assert layout['scrollWidth'] <= width, layout
                if width >= 1366:
                    assert layout['chartTop'] < 460 and layout['chartBottom'] < height, layout
                layouts.append(layout)
                page.screenshot(path=str(out/f'history-{width}.png'), full_page=True)
            checks.append('desktop, laptop, tablet and mobile layout; complete trend visible on laptop first screen')

            page.set_viewport_size({'width':1366, 'height':768})
            page.goto(url+'/history')
            expect(page.locator('#historyPanel')).to_have_attribute('data-state', 'ready')
            for key, title, unit in [('temp_c','温度','°C'),('rh','相对湿度','%RH'),('co2_ppm','CO₂','ppm'),('smoke_raw','烟雾相对值','ADC')]:
                page.locator(f'[data-history-metric="{key}"]').click()
                expect(page.locator('#historyMetricTitle')).to_have_text(title)
                expect(page.locator('.statUnit').first).to_have_text(unit)
                expect(page.locator(f'th[data-history-column="{key}"]')).to_have_class('is-selected')
            checks.append('four metrics update statistics, threshold labels and highlighted sample column')

            page.get_by_role('button', name='24 小时', exact=True).click()
            expect(page.locator('#historyMessage')).to_contain_text('1440')
            expect(page.locator('#statTotal')).not_to_have_text('--')
            page.locator('#historyMode').select_option('hybrid')
            expect(page.locator('#statTotal')).to_have_text('900')
            expected = page.request.get(url+'/api/history?minutes=1440&mode=hybrid').json()
            assert len(expected['points']) < 900
            expect(page.locator('#historyCount')).to_contain_text(str(len(expected['points']))+' 个展示点')
            page.locator('[data-history-metric="co2_ppm"]').click()
            expect(page.locator('#historyPanel')).to_have_attribute('data-state', 'missing')
            expect(page.locator('#statValid')).to_have_text('有效 0 · 缺失 900')
            expect(page.locator('#statAvg')).to_have_text('--')
            expect(page.locator('#sampleRows [data-history-column="co2_ppm"]').first).to_have_text('—')
            expect(page.locator('#exportHistory')).to_be_enabled()
            page.screenshot(path=str(out/'missing-metric.png'), full_page=True)
            checks.append('all-missing metric is distinct from zero or empty history; total uses all 900 samples')

            with page.expect_request('**/api/history/export?*') as exported_request:
                with page.expect_download() as download_info:
                    page.locator('#exportHistory').click()
            export = out/'history-export.csv'
            download_info.value.save_as(export)
            records = list(csv.reader(io.StringIO(export.read_text(encoding='utf-8-sig'))))
            assert len(records) == 901 and all(row[4] == '' for row in records[1:])
            assert 'from=' in exported_request.value.url and 'to=' in exported_request.value.url and 'minutes=' not in exported_request.value.url
            checks.append('CSV exports all samples with missing values blank and exact displayed query bounds')

            page.locator('#historyDate summary').click()
            page.locator('#historyFrom').fill('2026-01-01T00:00')
            page.locator('#historyTo').fill('2026-01-03T00:00')
            page.locator('#applyHistory').click()
            expect(page.locator('#historyRangeError')).to_contain_text('最多 24 小时')
            expect(page.locator('#statTotal')).to_have_text('900')
            page.locator('#historyTo').fill('2026-01-01T01:00')
            page.locator('#historyTo').press('Enter')
            expect(page.locator('#historyDate')).not_to_have_attribute('open', '')
            expect(page.locator('#historyPanel')).to_have_attribute('data-state', 'empty')
            expect(page.locator('#statTotal')).to_have_text('0')
            expect(page.locator('#exportHistory')).to_be_disabled()
            expect(page.locator('#historyRangeText')).to_contain_text('2026/01/01')
            page.screenshot(path=str(out/'empty-history.png'), full_page=True)
            page.locator('#historyDate summary').click()
            page.keyboard.press('Escape')
            expect(page.locator('#historyDate')).not_to_have_attribute('open', '')
            expect(page.locator('#historyDate summary')).to_be_focused()
            page.locator('#historyDate summary').click()
            page.locator('#resetHistory').click()
            expect(page.locator('[data-minutes="10"]')).to_have_attribute('aria-pressed','true')
            checks.append('custom dates validate 24-hour limit, submit with Enter, close with Escape and reset to relative range')

            page.locator('[data-history-metric="smoke_raw"]').click()
            expect(page.locator('#historyPanel')).to_have_attribute('data-state','ready')
            page.route('**/api/history?*', lambda route: route.fulfill(status=503, json={'error':'测试查询暂不可用'}))
            page.locator('#refreshHistory').click()
            expect(page.locator('#historyPanel')).to_have_attribute('data-state','error')
            expect(page.locator('#exportHistory')).to_be_disabled()
            expect(page.locator('#statMax')).to_have_text('--')
            page.screenshot(path=str(out/'query-error.png'), full_page=True)
            page.unroute('**/api/history?*')
            page.locator('#retryHistory').click()
            expect(page.locator('#historyPanel')).to_have_attribute('data-state','ready')
            expect(page.locator('#historyMessage')).to_be_empty()
            checks.append('query error clears stale statistics, disables export and supports successful retry')

            held = []
            def hold_live(route):
                if 'mode=live' in route.request.url:
                    held.append(route)
                else:
                    route.continue_()
            page.route('**/api/history?*', hold_live)
            page.locator('#historyMode').select_option('live')
            expect(page.locator('#historyPanel')).to_have_attribute('data-state','loading')
            expect(page.locator('#exportHistory')).to_be_disabled()
            page.locator('#historyMode').select_option('hybrid')
            expect(page.locator('#historyPanel')).to_have_attribute('data-state','ready')
            expected_total = page.locator('#statTotal').inner_text()
            assert held
            for route in held:
                route.fulfill(json={'mode':'live','points':[],'since':now-600,'until':now,
                    'statistics':{'total':0,'metrics':{key:{'valid':0,'min':None,'max':None,'avg':None} for key in ['temp_c','rh','co2_ppm','smoke_raw']}}})
            page.wait_for_timeout(250)
            expect(page.locator('#statTotal')).to_have_text(expected_total)
            expect(page.locator('#historyMode')).to_have_value('hybrid')
            page.unroute('**/api/history?*')
            checks.append('rapid mode switches ignore out-of-order responses and hide stale data while loading')

            page.set_viewport_size({'width':390,'height':844})
            page.locator('#historyDate summary').click()
            assert page.locator('.history-date-popover').evaluate('(el)=>el.getBoundingClientRect().left') >= 0
            assert page.evaluate('document.documentElement.scrollWidth') == 390
            page.screenshot(path=str(out/'mobile-date-picker.png'), full_page=True)
            browser.close()
        assert not errors, errors
        report = {'status':'passed','checks':checks,'layouts':layouts,'javascript_errors':errors,
                  'data':'isolated simulated fixtures','real_email_sent':False,'hardware_connected':False}
        (out/'history-ui-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        server.shutdown()
        runtime.stop()


if __name__ == '__main__':
    main()
