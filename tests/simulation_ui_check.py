"""Simulation UI checks with isolated data; no physical devices or external mail."""
import copy
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
    out = ROOT / 'artifacts/simulation-check'
    out.mkdir(parents=True, exist_ok=True)
    runtime = MonitorRuntime(out / ('data-' + uuid.uuid4().hex), mode='demo', mail=MailSettings())
    runtime.tick()
    app = create_app(runtime, start_runtime=False)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}'
    fixture = app.test_client().get('/api/status').json
    errors, checks, layouts, posts = [], [], [], []
    reject_control = False

    def apply_control(route):
        body = route.request.post_data_json
        posts.append(body)
        if reject_control:
            route.fulfill(status=403, json={'error':'页面已过期，请刷新后再操作'})
            return
        runtime.configure(**body)
        fixture.update(runtime.snapshot())
        route.fulfill(json=fixture)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={'width':1366,'height':768}, timezone_id='Asia/Shanghai')
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/api/status', lambda route: route.fulfill(json=fixture))
            page.route('**/api/simulation', apply_control)
            for width,height in [(1366,768),(1920,1080),(768,1024),(390,844),(320,760)]:
                page.set_viewport_size({'width':width,'height':height})
                page.goto(url+'/simulation')
                expect(page.locator('#simulationWorkspace')).to_have_attribute('data-state','ready')
                expect(page.locator('[data-scenario]')).to_have_count(6)
                expect(page.locator('[data-scenario="normal"]')).to_have_attribute('aria-pressed','true')
                expect(page.locator('#modeSelect')).to_have_value('demo')
                expect(page.locator('#mailLinkState')).to_have_text('未启用')
                expect(page.locator('#simSoundState')).to_have_text('待联调')
                expect(page.locator('#visionIndicatorText')).to_have_text('明火检测未启用')
                layout = page.evaluate('''() => ({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
                    listBottom:document.querySelector('.sim-scenario-list').getBoundingClientRect().bottom,
                    monitorBottom:document.querySelector('.sim-monitor').getBoundingClientRect().bottom,
                    indicatorWidth:document.querySelector('#simIndicator').getBoundingClientRect().width})''')
                assert layout['scrollWidth']<=width and layout['indicatorWidth']>0, layout
                if width>=1366:
                    assert layout['listBottom']<height and layout['monitorBottom']<height, layout
                layouts.append(layout)
                page.screenshot(path=str(out/f'simulation-{width}.png'), full_page=width<1000)
            checks.append('five widths; shared shell; visible desktop controls and feedback; valid smoke scale')

            page.set_viewport_size({'width':1366,'height':768})
            for scenario,level in [('warning','yellow'),('alarm','red'),('recovery','green'),('disconnected','gray'),('rising','green'),('normal','green')]:
                button = page.locator(f'[data-scenario="{scenario}"]')
                expect(button).to_be_enabled()
                button.focus()
                page.keyboard.press('Enter')
                expect(button).to_have_attribute('aria-pressed','true')
                expect(page.locator('#simReadingPanel')).to_have_attribute('data-level',level)
                expect(page.locator('#controlMessage')).to_have_text('已应用，后台持续采样')
                expect(page.locator('#controlMessage')).to_have_attribute('data-state','success')
                assert posts[-1] == {'scenario':scenario}
                if scenario=='disconnected':
                    expect(page.locator('#simValue')).to_have_text('--')
                    expect(page.locator('#simSamplingStatus')).to_have_text('数据待接入')
            checks.append('all six scenarios support keyboard operation and confirmed selection; invalid smoke is not zero')

            page.locator('#modeSelect').select_option('hybrid')
            expect(page.locator('#simModeHint')).to_contain_text('其他指标由设备上报')
            expect(page.locator('#simState')).to_contain_text('模拟数据')
            page.locator('#modeSelect').select_option('live')
            expect(page.locator('#scenarioLabel')).to_have_text('真实采集中')
            expect(page.locator('[data-scenario][aria-pressed="true"]')).to_have_count(0)
            expect(page.locator('#simValue')).to_have_text('--')
            for button in page.locator('[data-scenario]').all():
                expect(button).to_be_disabled()
            reject_control = True
            page.locator('#modeSelect').select_option('demo')
            expect(page.locator('#controlMessage')).to_contain_text('页面已过期')
            expect(page.locator('#controlMessage')).to_have_attribute('data-state','error')
            expect(page.locator('#modeSelect')).to_have_value('live')
            expect(page.locator('[data-scenario="alarm"]')).to_be_disabled()
            reject_control = False
            page.locator('#modeSelect').select_option('demo')
            expect(page.locator('[data-scenario="alarm"]')).to_be_enabled()
            checks.append('hybrid and live source descriptions; live mode disables injection; rejected changes retain confirmed mode')

            page.unroute('**/api/status')
            page.route('**/api/status', lambda route: route.fulfill(status=503,json={'error':'offline'}))
            page.locator('#refreshSimulation').click()
            expect(page.locator('#simulationWorkspace')).to_have_attribute('data-state','error')
            expect(page.locator('#simValue')).to_have_text('--')
            expect(page.locator('#modeSelect')).to_be_disabled()
            expect(page.locator('#simFeedback')).to_contain_text('恢复连接')
            expect(page.locator('#mailLinkState')).to_have_text('状态未知')
            expect(page.locator('[data-scenario][aria-pressed="true"]')).to_have_count(0)
            page.screenshot(path=str(out/'offline.png'))
            page.unroute('**/api/status')
            page.route('**/api/status', lambda route: route.fulfill(json=fixture))
            page.locator('#refreshSimulation').click()
            expect(page.locator('#simulationWorkspace')).to_have_attribute('data-state','ready')
            expect(page.locator('#modeSelect')).to_be_enabled()
            expect(page.locator('#simFeedback')).to_be_empty()
            checks.append('offline clears live data and disables controls; refresh restores confirmed state')

            fixture['mail'].update(enabled=True,allow_demo=False)
            page.locator('#refreshSimulation').click()
            expect(page.locator('#mailLinkState')).to_have_text('已启用')
            expect(page.locator('#simMailNote')).to_have_text('当前不发送模拟事件邮件。')
            fixture['mail']['config_error'] = True
            page.locator('#refreshSimulation').click()
            expect(page.locator('#mailLinkState')).to_have_attribute('data-state','error')
            expect(page.locator('#simMailNote')).to_contain_text('配置有误')
            page.route('**/api/notification_events*', lambda route:route.fulfill(status=503,json={}))
            page.locator('#refreshSimulation').click()
            expect(page.locator('#popupState')).to_have_text('读取异常')
            expect(page.locator('#popupState')).to_have_attribute('data-state','error')
            page.unroute('**/api/notification_events*')
            page.locator('#refreshSimulation').click()
            expect(page.locator('#popupState')).to_have_text('监听中')
            checks.append('mail configuration and demo-mail policy are distinct; notification listener failure and recovery are visible')

            old = copy.deepcopy(fixture)
            pending_status, pending_controls = [], []
            page.unroute('**/api/status')
            page.route('**/api/status', lambda route:pending_status.append(route))
            page.locator('#refreshSimulation').click()
            deadline = time.monotonic()+4
            while not pending_status and time.monotonic()<deadline:
                page.wait_for_timeout(50)
            assert pending_status
            page.unroute('**/api/simulation')
            page.route('**/api/simulation', lambda route:pending_controls.append(route))
            page.locator('[data-scenario="alarm"]').click()
            expect(page.locator('#controlMessage')).to_have_attribute('data-state','pending')
            expect(page.locator('#modeSelect')).to_be_disabled()
            expect(page.locator('[data-scenario="normal"]')).to_be_disabled()
            deadline = time.monotonic()+4
            while not pending_controls and time.monotonic()<deadline:
                page.wait_for_timeout(50)
            assert len(pending_controls)==1
            apply_control(pending_controls[0])
            expect(page.locator('#simReadingPanel')).to_have_attribute('data-level','red')
            expect(page.locator('#controlMessage')).to_have_attribute('data-state','success')
            for route in pending_status:
                route.fulfill(json=old)
            page.wait_for_timeout(100)
            expect(page.locator('[data-scenario="alarm"]')).to_have_attribute('aria-pressed','true')
            expect(page.locator('#simReadingPanel')).to_have_attribute('data-level','red')
            page.unroute('**/api/status')
            page.route('**/api/status', lambda route:route.fulfill(json=fixture))
            page.screenshot(path=str(out/'alarm.png'))
            checks.append('pending commands lock controls; a delayed status response cannot undo the applied scenario')

            page.locator('.sim-guide summary').click()
            expect(page.locator('.sim-guide')).to_have_attribute('open','')
            expect(page.locator('.sim-guide')).to_contain_text('15 秒')
            page.locator('.sim-result-links').get_by_role('link',name='查看告警记录').click()
            expect(page).to_have_url(url+'/alerts')
            page.goto(url+'/simulation')
            page.locator('.sim-result-links').get_by_role('link',name='历史趋势').click()
            expect(page).to_have_url(url+'/history')
            checks.append('validation guidance expands and both result links reach their existing pages')
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        runtime.stop()
    report={'status':'passed','checks':checks,'layouts':layouts,'javascript_errors':errors,
            'data':'isolated response fixtures','real_email_sent':False,'hardware_connected':False}
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
