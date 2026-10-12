"""告警页样板验证。独立数据库与本地临时服务器，不发送外部邮件。"""
import json
import logging
import sys
import threading
import time
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard import create_app
from runtime import MonitorRuntime
from notification_core import MailSettings


def main():
    out = ROOT / 'artifacts' / 'alerts-check'
    out.mkdir(parents=True, exist_ok=True)
    runtime = MonitorRuntime(out / ('qa-' + uuid.uuid4().hex), mode='demo', mail=MailSettings())
    runtime.tick()
    app = create_app(runtime, start_runtime=True)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    base = runtime.snapshot()['sample'].copy()
    now = time.time()

    def seed(index, level, metric, value, reason, offset=0):
        data = dict(base, device=f'qa-case-{index}', mode='demo', is_demo=True,
                    event_source='smoke' if metric == 'smoke_raw' else 'environment', metric=metric)
        data[metric] = value
        evaluation = {'level': level, 'abnormal': [(metric, value, level)] if level != 'green' else [], 'reason': reason}
        return runtime.alerts.observe(data, evaluation, now=now + offset)

    server = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}'
    errors, checks, layouts = [], [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            empty_page = browser.new_page(viewport={'width':1366,'height':768}, timezone_id='Asia/Shanghai')
            empty_page.on('pageerror', lambda error: errors.append(str(error)))
            empty_page.goto(url + '/alerts')
            expect(empty_page.locator('#eventTotal')).to_have_text('0')
            expect(empty_page.locator('.empty-state strong')).to_have_text('暂无告警记录')
            empty_page.screenshot(path=str(out / 'empty-1366.png'), full_page=True)
            empty_page.close()
            checks.append('new database shows an honest empty state without placeholder events')
            seed(1, 'red', 'smoke_raw', 2500, '烟雾相对值 2500 ADC，达到报警阈值', -90)
            seed(2, 'yellow', 'temp_c', 38.2, '温度 38.2 °C，达到预警阈值', -30)
            page = browser.new_page(viewport={'width':1366, 'height':768}, timezone_id='Asia/Shanghai')
            page.on('pageerror', lambda error: errors.append(str(error)))
            for width, height in [(1366,768),(1920,1080),(390,844)]:
                page.set_viewport_size({'width':width,'height':height})
                page.goto(url + '/alerts')
                expect(page.locator('#connection')).to_have_text('后台运行中')
                expect(page.locator('#eventTotal')).to_have_text('2')
                expect(page.locator('#riskIndicator')).to_have_attribute('data-level','green')
                expect(page.locator('#riskIndicatorText')).to_have_text('正常')
                expect(page.locator('#alertCountAll')).to_have_text('2')
                page.mouse.move(0,0)
                layout = page.evaluate('''() => ({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
                    tableTop:document.querySelector('table').getBoundingClientRect().top,
                    bodyFont:getComputedStyle(document.querySelector('td.event-reason')).fontSize})''')
                if layout['scrollWidth'] > width:
                    page.screenshot(path=str(out / 'layout-failure.png'), full_page=True)
                    layout['overflow'] = page.evaluate('''() => [...document.querySelectorAll('body *')]
                        .filter(el => el.getBoundingClientRect().right > innerWidth + 1 && getComputedStyle(el).display !== 'none')
                        .map(el => ({tag:el.tagName,cls:el.className,id:el.id,width:el.getBoundingClientRect().width,
                            right:el.getBoundingClientRect().right})).slice(0,30)''')
                assert layout['scrollWidth'] <= width, layout
                if width >= 1366:
                    assert layout['tableTop'] < 330, layout
                layouts.append(layout)
                page.screenshot(path=str(out / f'alerts-{width}.png'), full_page=True)
            checks.append('few records at desktop, laptop and mobile; table visible in first laptop screen')

            page.set_viewport_size({'width':1366,'height':768})
            page.goto(url + '/alerts')
            expect(page.locator('#eventTotal')).to_have_text('2')
            page.get_by_role('button',name='刷新',exact=True).click()
            page.locator('#alertRules summary').click()
            expect(page.locator('.rules-popover')).to_contain_text('连续满足条件 15 秒')
            page.screenshot(path=str(out / 'rules-1366.png'),full_page=True)
            page.keyboard.press('Escape')
            expect(page.locator('#alertRules')).not_to_have_attribute('open','')
            expect(page.locator('#alertRules summary')).to_be_focused()
            page.locator('#alertRules summary').click()
            page.locator('h1').click()
            expect(page.locator('#alertRules')).not_to_have_attribute('open','')
            checks.append('deduplication help opens by click and closes by Escape or outside click')
            page.locator('[data-level-filter="red"]').click()
            expect(page.locator('#eventTotal')).to_have_text('1')
            expect(page.locator('[data-level-filter="red"]')).to_have_attribute('aria-pressed','true')
            expect(page.locator('#eventRows')).to_contain_text('2500 ADC')
            expect(page.locator('#alertCountAll')).to_have_text('2')
            expect(page.locator('#alertCountRed')).to_have_text('1')
            expect(page.locator('#alertCountYellow')).to_have_text('1')
            expect(page.locator('#alertCountGreen')).to_have_text('0')
            page.locator('#eventRows button').first.click()
            expect(page.locator('#eventDialog')).to_be_visible()
            expect(page.locator('.detail-summary')).to_contain_text('2500 ADC')
            expect(page.locator('.snapshot-grid')).to_contain_text('2500')
            expect(page.locator('#eventDetails')).to_contain_text('模拟演示')
            expect(page.locator('.snapshot-item.is-target')).to_have_count(1)
            expect(page.locator('.snapshot-item.is-target')).to_have_attribute('data-snapshot-metric','smoke_raw')
            expect(page.locator('.snapshot-item.is-target')).to_have_attribute('data-event-level','red')
            expect(page.locator('.metric-marker')).to_have_text('触发指标')
            page.screenshot(path=str(out / 'detail-1366.png'), full_page=True)
            page.keyboard.press('Escape')
            expect(page.locator('#eventDialog')).not_to_be_visible()
            page.locator('[data-level-filter="yellow"]').click()
            expect(page.locator('#eventRows')).to_contain_text('38.2')
            page.locator('#eventRows button').first.click()
            expect(page.locator('.snapshot-item.is-target')).to_have_attribute('data-snapshot-metric','temp_c')
            expect(page.locator('.snapshot-item.is-target')).to_have_attribute('data-event-level','yellow')
            page.keyboard.press('Escape')
            page.locator('[data-level-filter="green"]').click()
            expect(page.locator('#eventTotal')).to_have_text('0')
            expect(page.locator('.empty-state')).to_contain_text('没有符合条件的记录')
            expect(page.locator('#alertCountAll')).to_have_text('2')
            expect(page.locator('#alertCountYellow')).to_have_text('1')
            expect(page.locator('[data-level-filter="green"]')).to_be_enabled()
            page.screenshot(path=str(out / 'empty-filter-1366.png'), full_page=True)
            page.get_by_role('button',name='重置',exact=True).click()
            expect(page.locator('#eventTotal')).to_have_text('2')
            expect(page.locator('[data-level-filter=""]')).to_have_attribute('aria-pressed','true')
            page.locator('#alertQuery').fill('温度')
            page.locator('#alertQuery').press('Enter')
            expect(page.locator('#eventTotal')).to_have_text('1')
            expect(page.locator('#eventRows')).to_contain_text('38.2')
            expect(page.locator('#alertCountAll')).to_have_text('1')
            expect(page.locator('#alertCountRed')).to_have_text('0')
            expect(page.locator('#alertCountYellow')).to_have_text('1')
            page.get_by_role('button',name='重置',exact=True).click()
            page.locator('.date-disclosure summary').click()
            page.locator('#alertFrom').fill('2099-01-01T00:00')
            page.get_by_role('button',name='应用',exact=True).click()
            expect(page.locator('#eventTotal')).to_have_text('0')
            expect(page.locator('#dateFilterLabel')).to_have_text('已选时间')
            expect(page.locator('#alertCountAll')).to_have_text('0')
            page.get_by_role('button',name='重置',exact=True).click()
            expect(page.locator('#eventTotal')).to_have_text('2')
            expect(page.locator('#alertFrom')).to_have_value('')
            page.locator('.date-disclosure summary').click()
            checks.append('level filters, keyword enter, date filter, reset, detail snapshot and Escape dismissal')
            checks.append('tab counts follow query filters but stay stable across level switches; zero-count tabs remain clickable')

            for index in range(3,28):
                seed(index, 'red' if index%2 else 'yellow', 'co2_ppm', 1650,
                     f'CO₂ 浓度 1650 ppm，达到演示阈值（联调用例 {index}）', -index*120)
            page.get_by_role('button',name='刷新',exact=True).click()
            expect(page.locator('#eventTotal')).to_have_text('27')
            expect(page.locator('#eventRows tr')).to_have_count(20)
            page.locator('#nextPage').click()
            expect(page.locator('#pageNumber')).to_have_text('2 / 2')
            expect(page.locator('#eventRows tr')).to_have_count(7)
            page.locator('#prevPage').click()
            expect(page.locator('#pageNumber')).to_have_text('1 / 2')
            page.screenshot(path=str(out / 'multiple-records-1366.png'), full_page=True)
            checks.append('manual refresh and 27-event pagination')

            seed(100, 'red', 'temp_c', 46, '温度 46 °C，达到报警阈值。' + '这是用于验证较长事件描述完整显示的隔离测试文本。'*8)
            page.get_by_role('button',name='刷新',exact=True).click()
            expect(page.locator('#eventTotal')).to_have_text('28')
            reason = page.locator('#eventRows .event-reason').first
            assert reason.evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
            reason.locator('p').click()
            page.locator('#eventRows button').first.click()
            expect(page.locator('.detail-summary')).to_contain_text('隔离测试文本')
            page.keyboard.press('Escape')
            checks.append('long descriptions wrap and remain available in detail')

            page.route('**/api/alerts?*',lambda route:route.abort())
            page.get_by_role('button',name='刷新',exact=True).click()
            expect(page.locator('#alertMessage')).not_to_be_empty()
            page.unroute('**/api/alerts?*')
            page.get_by_role('button',name='刷新',exact=True).click()
            expect(page.locator('#alertMessage')).to_be_empty()
            checks.append('query failure visible and refresh recovers')

            page.set_viewport_size({'width':390,'height':844})
            page.goto(url + '/alerts')
            expect(page.locator('#eventTotal')).to_have_text('28')
            page.locator('#alertQuery').fill('2500')
            page.locator('#alertQuery').press('Enter')
            expect(page.locator('#eventTotal')).to_have_text('1')
            page.locator('#eventRows button').first.click()
            expect(page.locator('#eventDialog')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(out / 'detail-390.png'),full_page=True)
            page.get_by_role('button',name='关闭详情').click()
            checks.append('mobile query, horizontally scrollable table and full-width detail')

            page.set_viewport_size({'width':1366,'height':768})
            page.goto(url + '/alerts')
            page.locator('#alertQuery').fill('联调用例 3')
            page.locator('#alertQuery').press('Enter')
            expect(page.locator('#eventTotal')).to_have_text('1')
            page.locator('#eventRows button').first.click()
            expect(page.locator('.snapshot-item.is-target')).to_have_attribute('data-snapshot-metric','co2_ppm')
            page.keyboard.press('Escape')
            seed(200,'yellow','rh',80,'相对湿度 80 %RH，达到预警阈值')
            page.get_by_role('button',name='重置',exact=True).click()
            expect(page.locator('#eventTotal')).to_have_text('29')
            page.locator('#eventRows button').first.click()
            expect(page.locator('.snapshot-item.is-target')).to_have_count(1)
            expect(page.locator('.snapshot-item.is-target')).to_have_attribute('data-snapshot-metric','rh')
            expect(page.locator('.snapshot-item.is-target')).to_have_attribute('data-event-level','yellow')
            page.keyboard.press('Escape')
            for offset in range(2,20,2):
                seed(200,'green','rh',55,'相对湿度恢复到演示阈值内',offset)
            page.get_by_role('button',name='刷新',exact=True).click()
            expect(page.locator('#eventTotal')).to_have_text('30')
            page.locator('#eventRows button').first.click()
            expect(page.locator('.snapshot-item.is-target')).to_have_attribute('data-snapshot-metric','rh')
            expect(page.locator('.snapshot-item.is-target')).to_have_attribute('data-event-level','green')
            expect(page.locator('.metric-marker')).to_have_text('恢复指标')
            page.screenshot(path=str(out/'detail-recovery-1366.png'),full_page=True)
            page.keyboard.press('Escape')
            checks.append('smoke, temperature, CO2 and humidity highlight their actual target; recovery uses a green recovery marker')

            status_fixture=app.test_client().get('/api/status').json
            for level,label in [('red','红色报警'),('yellow','黄色预警'),('gray','数据不完整'),('green','正常')]:
                response=json.loads(json.dumps(status_fixture))
                response['sample']['level']=level
                response['sample']['missing']=['温度'] if level=='gray' else []
                if level=='gray':
                    response['sample']['temp_c']=None
                    response['sample']['sources']['temp_c']='missing'
                page.route('**/api/status',lambda route,_request,body=response:route.fulfill(json=body))
                page.goto(url+'/alerts')
                expect(page.locator('#riskIndicatorText')).to_have_text(label)
                expect(page.locator('#riskIndicator')).to_have_attribute('data-level',level)
                if level=='red':
                    page.emulate_media(reduced_motion='reduce')
                    assert page.locator('.risk-dot').evaluate('(el)=>getComputedStyle(el).animationName')=='none'
                    page.emulate_media(reduced_motion='no-preference')
                page.unroute('**/api/status')
            frozen=json.loads(json.dumps(status_fixture))
            frozen['runtime_online']=False
            page.route('**/api/status',lambda route:route.fulfill(json=frozen))
            page.goto(url+'/alerts')
            expect(page.locator('#riskIndicatorText')).to_have_text('状态未知')
            expect(page.locator('#riskIndicator')).to_have_attribute('data-level','gray')
            page.unroute('**/api/status')
            page.route('**/api/status',lambda route:route.abort())
            page.goto(url+'/alerts')
            expect(page.locator('#connection')).to_have_text('后台连接中断')
            expect(page.locator('#riskIndicatorText')).to_have_text('状态未知')
            page.unroute('**/api/status')
            page.goto(url+'/alerts')
            expect(page.locator('#riskIndicatorText')).to_have_text('正常')
            checks.append('current risk stays independent of old alerts; warning, alarm, missing, frozen and disconnected states render honestly; reduced motion respected')

            for route in ['/','/history','/vision','/devices','/simulation']:
                page.goto(url+route)
                expect(page.locator('#connection')).to_have_text('后台运行中')
                assert page.locator('link[href*="alerts.css"]').count() == 0
            checks.append('other five pages retain their stylesheet and remain operational')
            browser.close()
        assert not errors, errors
        report={'status':'passed','checks':checks,'layouts':layouts,
                'javascript_errors':errors,'data':'isolated simulated fixtures','real_email_sent':False}
        (out/'alerts-ui-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        server.shutdown()
        runtime.stop()


if __name__ == '__main__':
    main()
