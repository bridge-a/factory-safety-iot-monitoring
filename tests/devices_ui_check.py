"""Device UI checks use isolated data and response fixtures, never physical devices or SMTP."""
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
    out = ROOT / 'artifacts/devices-check'
    out.mkdir(parents=True, exist_ok=True)
    runtime = MonitorRuntime(out / ('data-' + uuid.uuid4().hex), mode='demo', mail=MailSettings())
    runtime.tick()
    app = create_app(runtime, start_runtime=False)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}'
    errors, checks, layouts = [], [], []
    initial = app.test_client().get('/api/status').json
    fixture = copy.deepcopy(initial)
    queue = {'mail_counts':{}}

    def value(page, key):
        return page.locator(f'[data-device-reading="{key}"] [data-device-value]')

    def source(page, key):
        return page.locator(f'[data-device-source="{key}"]')

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={'width':1366, 'height':768}, timezone_id='Asia/Shanghai')
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/api/status', lambda route: route.fulfill(json=fixture))
            page.route('**/api/notification_status', lambda route: route.fulfill(json=queue))
            for width,height in [(1366,768),(1920,1080),(768,1024),(390,844),(320,760)]:
                page.set_viewport_size({'width':width,'height':height})
                page.goto(url+'/devices')
                expect(page.locator('#devicesWorkspace')).to_have_attribute('data-state','ready')
                expect(page.locator('#deviceRows>tr')).to_have_count(6)
                expect(page.locator('#deviceEsp')).to_have_text('未接入')
                expect(page.locator('#deviceRows [data-state="simulation"]')).to_have_count(10)
                expect(page.locator('#deviceMail')).to_have_text('未启用')
                expect(page.locator('#queuePending')).to_have_text('0')
                expect(page.locator('#visionIndicatorText')).to_have_text('明火检测未启用')
                layout = page.evaluate('''() => ({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
                    tableTop:document.querySelector('.devices-table').getBoundingClientRect().top,
                    tableBottom:document.querySelector('.devices-table').getBoundingClientRect().bottom})''')
                assert layout['scrollWidth'] <= width, layout
                page.screenshot(path=str(out/f'devices-{width}.png'), full_page=True)
                if width>=1366:
                    assert layout['tableTop'] < 350 and layout['tableBottom'] < height, layout
                layouts.append(layout)
            checks.append('five widths; six configuration rows; simulated readings never mark the actual node online')

            page.set_viewport_size({'width':1366,'height':768})
            fixture['sound_light'] = {'state':'ready','detail':'黄色预警 · 本地控制'}
            page.locator('#refreshDevices').click()
            expect(page.locator('#deviceSoundState')).to_have_text('黄色预警 · 本地控制')
            fixture['sound_light'] = {'state':'offline','detail':'设备离线，声光状态未知'}
            page.locator('#refreshDevices').click()
            expect(page.locator('#deviceSoundState')).to_have_text('设备离线')
            checks.append('device-reported actuator state and offline state replace the old placeholder')
            fixture = copy.deepcopy(initial)
            fixture['mode'] = 'hybrid'
            fixture['mqtt'].update(connected=True,detail='已连接',topic='factory/workshop1/env')
            fixture['sample'].update(mode='hybrid',esp32_online=True,hardware_device='fixture-esp32-s3',
                                     received_at=time.time(),temp_c=27.2,rh=None,co2_ppm=725,lux=282.4)
            fixture['sample']['sources'].update(temp_c='mqtt',rh='missing',co2_ppm='mqtt',smoke_raw='simulation')
            page.goto(url+'/devices')
            expect(page.locator('#deviceEsp')).to_have_text('在线')
            expect(page.locator('#deviceMqttState')).to_have_text('已连接')
            expect(value(page,'temp_c')).to_have_text('27.2')
            expect(source(page,'temp_c')).to_have_text('实际采集')
            expect(value(page,'rh')).to_have_text('—')
            expect(source(page,'rh')).to_have_text('无有效数据')
            expect(source(page,'smoke_raw')).to_have_text('模拟数据')
            expect(source(page,'lux')).to_have_text('实际采集')
            expect(page.locator('[data-device-row="sht31"]')).to_have_count(1)
            expect(page.locator('#deviceReceived')).not_to_have_text('尚无上报')
            page.screenshot(path=str(out/'mixed-fixture.png'))
            checks.append('mixed mode and partially missing SHT31 readings retain independent sources and valid values')

            fixture['mode'] = fixture['sample']['mode'] = 'live'
            fixture['sample']['esp32_online'] = False
            fixture['sample']['received_at'] = time.time()-45
            for key in ['temp_c','rh','co2_ppm','smoke_raw','lux']:
                fixture['sample'][key] = None
                if key != 'lux':
                    fixture['sample']['sources'][key] = 'missing'
            page.locator('#refreshDevices').click()
            expect(page.locator('#deviceEsp')).to_have_text('离线')
            expect(page.locator('#deviceMqttState')).to_have_text('已连接')
            for key in ['temp_c','rh','co2_ppm','smoke_raw','lux']:
                expect(value(page,key)).to_have_text('—')
                expect(source(page,key)).to_have_text('无有效数据')
            checks.append('broker connected is separate from stale/offline node; missing readings are never zero')

            fixture = copy.deepcopy(initial)
            fixture['sample']['smoke_raw'] = None
            fixture['mail'].update(enabled=True,allow_demo=True)
            queue = {'mail_counts':{'pending':2,'sending':1,'sent':8,'failed':3}}
            page.locator('#refreshDevices').click()
            expect(source(page,'smoke_raw')).to_have_text('模拟 · 无数据')
            expect(page.locator('#deviceMail')).to_have_text('已启用')
            expect(page.locator('#deviceDemoMail')).to_have_text('允许发送')
            for id_,count in [('queuePending','2'),('queueSending','1'),('queueSent','8'),('queueFailed','3')]:
                expect(page.locator('#'+id_)).to_have_text(count)
            expect(page.locator('#queueFailed')).to_have_attribute('data-failed','true')
            fixture['mail']['config_error'] = True
            page.locator('#refreshDevices').click()
            expect(page.locator('#deviceMail')).to_have_text('配置有误')
            expect(page.locator('#deviceMail')).to_have_attribute('data-state','error')
            checks.append('invalid simulated reading, mail configuration error and four distinct queue counters')

            page.unroute('**/api/notification_status')
            page.route('**/api/notification_status',lambda route:route.fulfill(status=503,json={'error':'unavailable'}))
            page.locator('#refreshDevices').click()
            expect(page.locator('#deviceQueueMessage')).to_contain_text('读取失败')
            expect(page.locator('#queueSent')).to_have_text('—')
            expect(page.locator('#devicesWorkspace')).to_have_attribute('data-state','ready')
            page.unroute('**/api/notification_status')
            page.route('**/api/notification_status',lambda route:route.fulfill(json=queue))
            page.unroute('**/api/status')
            page.route('**/api/status',lambda route:route.fulfill(status=503,json={'error':'offline'}))
            page.locator('#refreshDevices').click()
            expect(page.locator('#devicesWorkspace')).to_have_attribute('data-state','error')
            expect(page.locator('#deviceEsp')).to_have_text('状态未知')
            expect(page.locator('#deviceRows>tr')).to_have_count(6)
            expect(page.locator('#deviceMail')).to_have_text('未知')
            expect(page.locator('#queueSent')).to_have_text('—')
            for key in ['temp_c','rh','co2_ppm','smoke_raw','lux']:
                expect(value(page,key)).to_have_text('—')
            page.screenshot(path=str(out/'offline.png'))
            page.unroute('**/api/status')
            fixture = copy.deepcopy(initial)
            page.route('**/api/status',lambda route:route.fulfill(json=fixture))
            page.locator('#refreshDevices').click()
            expect(page.locator('#devicesWorkspace')).to_have_attribute('data-state','ready')
            expect(page.locator('#deviceQueueMessage')).to_be_empty()
            expect(page.locator('#queueSent')).to_have_text('8')
            checks.append('queue failure does not erase devices; backend failure clears readings but preserves inventory; refresh recovers')

            pending = []
            page.unroute('**/api/notification_status')
            page.route('**/api/notification_status',lambda route:pending.append(route))
            page.reload()
            deadline = time.monotonic()+4
            while not pending and time.monotonic()<deadline:
                page.wait_for_timeout(50)
            assert len(pending)==1
            page.locator('#refreshDevices').click()
            deadline = time.monotonic()+4
            while len(pending)<2 and time.monotonic()<deadline:
                page.wait_for_timeout(50)
            assert len(pending)==2
            pending[1].fulfill(json={'mail_counts':{'sent':12}})
            expect(page.locator('#queueSent')).to_have_text('12')
            pending[0].fulfill(json={'mail_counts':{'sent':1}})
            page.wait_for_timeout(100)
            expect(page.locator('#queueSent')).to_have_text('12')
            page.unroute('**/api/notification_status')
            checks.append('late queue responses cannot overwrite a newer refresh')

            page.get_by_role('link',name='查看明火检测').click()
            expect(page).to_have_url(url+'/vision')
            page.goto(url+'/devices')
            page.locator('.devices-actions').get_by_role('link',name='模拟与联调').click()
            expect(page).to_have_url(url+'/simulation')
            checks.append('camera and simulation links open their existing functional pages')
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
