"""Capture report placeholders with real UI code and isolated, labelled fixtures.

No MonitorRuntime.start(), no MQTT client, no SMTP sender, no production database.
User screenshots are copied byte-for-byte to the ignored output directory.
"""
import argparse
import base64
from contextlib import closing
import copy
from datetime import datetime
import hashlib
import html
import json
import logging
import math
from pathlib import Path
import shutil
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from alert_rule import METRICS, evaluate_metric
from camera_capture import CameraCapture, CameraSettings
from dashboard import create_app
from notification_core import MailSettings
from runtime import MonitorRuntime

NORMAL = dict(temp_c=26.4, rh=48.6, co2_ppm=682, smoke_raw=368, lux=286)


class ReportRuntime(MonitorRuntime):
    def __init__(self, data):
        super().__init__(data, mode="demo", mail=MailSettings(enabled=True, allow_demo=False),
                         camera=CameraCapture(CameraSettings(enabled=True)))
        self.case = "normal"
        self.jpeg = b""
        self.frames = {}
        self.mqtt_connected = True  # Display fixture only; self.client stays None.
        self.mqtt_detail = "报告占位：模拟 MQTT 连接状态"
        self.sample = self.make_sample(NORMAL, time.time())
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def make_sample(self, values, ts):
        sample = dict(values, ts=ts, mode="demo", device="report-demo", hardware_device="demo-esp32-workshop1",
                      received_at=ts, esp32_online=self.case != "offline",
                      sources={key: "simulation" if values.get(key) is not None else "missing" for key in METRICS})
        sample.update(co2_valid=sample.get("co2_ppm") is not None, smoke_valid=sample.get("smoke_raw") is not None)
        sample["channels"] = {key: evaluate_metric(key, sample.get(key)) for key in METRICS}
        levels = [v["level"] for v in sample["channels"].values()]
        sample["level"] = next((x for x in ("red", "yellow", "gray") if x in levels), "green")
        sample["missing"] = [METRICS[k]["label"] for k in METRICS if sample.get(k) is None]
        return sample

    def snapshot(self):
        with self.lock:
            self.sample["ts"] = time.time()
            self.sample["received_at"] = time.time()-65 if self.case == "offline" else time.time()
            result = super().snapshot()
            active = self.case == "fire"
            missing = self.case == "camera_offline"
            result["vision"] = dict(enabled=True, model="YOLOv8n Fire / 320", state="offline" if missing else "alarm" if active else "ready",
                                    detail="摄像头画面中断，当前无法判断明火状态" if missing else
                                           "连续检测到明火，置信度 86%（模拟）" if active else "当前模拟画面未检测到明火",
                                    confidence=None if missing else .86 if active else 0., fps=None if missing else 2., alarm_active=active)
            if active:
                result["sample"]["level"] = "red"
            level = result["sample"]["level"]
            result["sound_light"] = dict(state="offline" if self.case == "offline" else "ready", effective_level=level,
                                         remote_active=True, detail="模拟反馈 · "+{"green":"已接入", "yellow":"黄色预警", "red":"红色报警", "gray":"数据缺失"}[level])
            return result

    def configure(self, mode=None, scenario=None):
        if mode not in (None, "demo"):
            raise ValueError("截图环境仅使用全模拟数据")
        if scenario:
            self.scenario = scenario
            name = {"normal":"normal", "rising":"smoke_yellow", "warning":"smoke_yellow", "alarm":"smoke_red", "recovery":"recovery", "disconnected":"missing"}[scenario]
            self.select(name)
        return copy.deepcopy(self.sample)

    def select(self, case):
        self.case = case
        values = dict(NORMAL)
        for prefix, key, yellow, red in [("temp","temp_c",38.2,47.6),("rh","rh",79.2,89.4),("co2","co2_ppm",1236,1824),("smoke","smoke_raw",1458,2486)]:
            if case == prefix+"_yellow": values[key] = yellow
            if case == prefix+"_red": values[key] = red
        if case == "combined": values.update(temp_c=47.6, co2_ppm=1824, smoke_raw=2486)
        if case == "missing": values["smoke_raw"] = None
        if case == "offline": values = {key:None for key in NORMAL}
        with self.lock:
            self.sample = self.make_sample(values, time.time())
            self.mqtt_connected = case != "offline"
            self.jpeg = self.frames.get("fire" if case == "fire" else "normal", b"")
            if case == "camera_offline": self.camera._state("reconnecting", "模拟摄像头断线，正在重连")
            elif self.jpeg: self.camera._publish(self.jpeg)
        self.seed_history(values)

    def seed_history(self, current):
        now = time.time()
        rows = []
        for i in range(601):
            age = (600-i)*6
            bump = math.exp(-((age-240)/62)**2)
            values = dict(temp_c=round(26.4+.7*math.sin(i/18)+20*bump,1),
                          rh=round(48.6+1.8*math.sin(i/21)+40*bump,1),
                          co2_ppm=round(682+65*math.sin(i/19)+1100*bump),
                          smoke_raw=round(368+45*math.sin(i/13)+2200*bump), lux=286)
            if age < 90:
                weight = 1-age/90
                for key in METRICS:
                    values[key] = None if current.get(key) is None else round(values[key]*(1-weight)+current[key]*weight,1)
            rows.append((now-age, "demo", json.dumps(self.make_sample(values, now-age), ensure_ascii=False)))
        with closing(self.env.connect()) as db, db:
            db.execute("DELETE FROM samples")  # Only the unique report fixture DB.
            db.executemany("INSERT INTO samples(ts,mode,payload) VALUES(?,?,?)", rows)

    def event(self, key, level, value=None, age=0, device=None):
        now = time.time()-age
        values = dict(NORMAL)
        if key in METRICS and value is not None: values[key] = value
        data = self.make_sample(values, now)
        data.update(device=device or "report:"+key, metric=key, is_demo=True,
                    event_source="vision" if key == "fire" else "smoke" if key == "smoke_raw" else "environment")
        reason = ("连续检测到明火，置信度 86%（模拟）" if level == "red" else "明火检测恢复正常（模拟）") if key == "fire" else evaluate_metric(key, value if value is not None else NORMAL[key])["reason"]
        if key == "fire": data["vision"] = dict(model="YOLOv8n Fire / 320",confidence=.86 if level=="red" else 0.,boxes=[],threshold=.55)
        evaluation = dict(level=level, reason=reason, abnormal=[(key,value,level)] if level in {"red","yellow"} else [])
        event = self.alerts.observe(data,evaluation,mail_enabled=True,now=now,allow_demo=False)
        if event and key=="fire" and level=="red": (self.evidence_dir/f"{event['id']}.jpg").write_bytes(self.frames['fire'])
        return event

    def seed_events(self):
        for key, yellow, red, age in [("temp_c",38.2,47.6,1300),("co2_ppm",1236,1824,1000),("smoke_raw",1458,2486,700)]:
            self.event(key,"yellow",yellow,age)
            self.event(key,"red",red,age-35)
            for delta in range(0,19,2): self.event(key,"green",NORMAL[key],age-65-delta)
        self.fire_event = self.event("fire","red",.86,330)
        for delta in range(0,19,2): self.event("fire","green",0.,300-delta)
        self.event("rh","yellow",79.2,170)
        for delta in range(0,19,2): self.event("rh","green",NORMAL['rh'],130-delta)


def camera_fixture(page, flame=False):
    """Draw a schematic from scratch, never alter or fabricate a user photo."""
    return base64.b64decode(page.evaluate("""(flame) => {
      const c=document.createElement('canvas');c.width=1280;c.height=720;const g=c.getContext('2d');
      g.fillStyle='#233b33';g.fillRect(0,0,1280,720);
      g.strokeStyle='#ffffff0b';g.lineWidth=1;
      for(let x=0;x<1280;x+=48){g.beginPath();g.moveTo(x,0);g.lineTo(x,720);g.stroke();}
      for(let y=0;y<720;y+=48){g.beginPath();g.moveTo(0,y);g.lineTo(1280,y);g.stroke();}
      g.strokeStyle='#91ad9d';g.lineWidth=3;g.strokeRect(180,165,920,395);
      g.fillStyle='#304b40';g.fillRect(220,200,205,300);g.fillRect(895,220,160,240);
      g.fillStyle='#d1c29a';g.font='24px Microsoft YaHei';g.fillText('环境采集',260,248);g.fillText('声光联动',915,260);
      for(let i=0;i<3;i++){g.strokeStyle='#759d89';g.strokeRect(250,280+i*64,143,42);}
      g.fillStyle=flame?'#e66649':'#85ab8a';g.beginPath();g.arc(977,325,22,0,Math.PI*2);g.fill();
      g.fillStyle='#9fbaab';g.font='25px Microsoft YaHei';g.fillText('工厂模拟区 · 01',515,485);
      if(flame){
        g.fillStyle='#d97942';g.beginPath();g.moveTo(610,410);g.bezierCurveTo(550,355,615,300,615,250);g.bezierCurveTo(675,285,638,315,675,338);g.bezierCurveTo(687,320,686,310,686,301);g.bezierCurveTo(748,367,687,430,650,425);g.closePath();g.fill();
        g.fillStyle='#f4cd7b';g.beginPath();g.moveTo(625,410);g.quadraticCurveTo(610,360,652,335);g.quadraticCurveTo(652,380,679,397);g.quadraticCurveTo(660,435,625,410);g.fill();
        g.strokeStyle='#f16c55';g.lineWidth=4;g.strokeRect(550,225,205,225);g.fillStyle='#a83f30';g.fillRect(550,191,205,34);g.fillStyle='white';g.font='21px Microsoft YaHei';g.fillText('FIRE 86% · 模拟',562,215);
      }else{g.strokeStyle='#91ad9d';g.lineWidth=3;g.strokeRect(570,280,135,85);g.beginPath();g.moveTo(705,298);g.lineTo(750,275);g.lineTo(750,370);g.lineTo(705,349);g.stroke();}
      g.fillStyle='#dbe7df';g.font='30px Microsoft YaHei';g.fillText('报告占位画面 · 非现场拍摄',420,109);
      g.fillStyle='#9bb7a6';g.font='22px Microsoft YaHei';g.fillText('页面、检测结果与设备状态均为模拟，后续替换为实机截图',310,622);
      return c.toDataURL('image/jpeg',.92).split(',')[1];
    }""", flame))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT/"artifacts/课程设计报告配图_2026-10-16")
    parser.add_argument("--real-dir", type=Path, default=Path.home()/"AppData/Local/Temp")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    data = ROOT/"artifacts/report-capture-runtime"/uuid.uuid4().hex
    runtime = ReportRuntime(data)
    manifest = []
    real_specs = [
        ("codex-clipboard-8a0243b3-e9d5-49ba-8397-d6b3c01e36ba.png", "01_真实截图/01_监控总览_正常运行_真实.png", "监控总览：正常运行", "用户原始截图；真实采集、摄像头与模型运行；推荐用于系统总览。"),
        ("codex-clipboard-54ace5c7-5bb6-4879-9c8c-c549f50ca54b.png", "01_真实截图/02_监控总览_明火红色报警_真实回放测试.png", "监控总览：明火报警", "用户原始截图；摄像头拍摄手机火焰视频，含标框、实体 LED、事件与弹窗。属于真实系统的视频回放联调。"),
        ("codex-clipboard-68d4cc6b-7df4-4e42-87d2-5d899ee460f4.jpg", "01_真实截图/03_QQ邮件_明火红色报警_实际接收.jpg", "QQ 邮件：明火报警实际接收", "用户原始邮件截图；事件 #24，2026-10-15 23:22:24，北京时间；workshop1:vision 红色报警，事件置信度 83%。与真实总览中的明火报警时间一致，用于展示远程通知实际送达。"),
    ]
    for source, rel, title, note in real_specs:
        path = args.real_dir/source
        if not path.is_file():
            raise FileNotFoundError(f"Missing user screenshot: {path}; pass --real-dir")
        target = out/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)
        assert path.read_bytes()==target.read_bytes()
        manifest.append(dict(file=rel,title=title,kind="真实截图",note=note,source=source))

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app = create_app(runtime, start_runtime=False)
    server = make_server("127.0.0.1",0,app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    stop = threading.Event()
    def refresh_fixture():
        while not stop.wait(.25):
            if runtime.jpeg and runtime.case != "camera_offline": runtime.camera._publish(runtime.jpeg)
    producer=threading.Thread(target=refresh_fixture,daemon=True);producer.start()
    errors=[]
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch()
            context=browser.new_context(viewport={"width":1920,"height":1080},device_scale_factor=1.5,locale="zh-CN",timezone_id="Asia/Shanghai",accept_downloads=True)
            context.route("**/*",lambda route: route.continue_() if route.request.url.startswith(url) or route.request.url.startswith(("data:","blob:")) else route.abort())
            page=context.new_page();page.on("pageerror",lambda error: errors.append(str(error)))
            page.goto(url)
            runtime.frames={"normal":camera_fixture(page),"fire":camera_fixture(page,True)}
            runtime.select("normal");runtime.seed_events()

            def navigate(path="/", case=None):
                if case: runtime.select(case)
                page.set_viewport_size({"width":1920,"height":1080})
                page.goto(url+path,wait_until="domcontentloaded")
                expect(page.locator('#connection')).to_have_text('后台运行中')
                page.evaluate("document.fonts.ready")
                page.wait_for_timeout(850)
                if path.split('?')[0] in ('/','/history'):
                    page.wait_for_function("!document.querySelector('#historyCount')?.textContent.includes('读取')")
                if page.locator('[data-camera-stage]').count() and case!='camera_offline':
                    expect(page.locator('[data-camera-stage]')).to_have_attribute('data-camera-live','true')

            def shot(rel,title,note,whole=True,locator=None,keep_toast=False):
                path=out/rel;path.parent.mkdir(parents=True,exist_ok=True)
                page.mouse.move(1800,20)
                page.add_style_tag(content='*{animation:none!important;transition:none!important} .report-capture-note{position:fixed;left:190px;bottom:9px;z-index:999999;font:12px "Microsoft YaHei",sans-serif;color:#6b592d;background:#fff9e8;border:1px solid #e5d4a5;border-radius:4px;padding:5px 10px;pointer-events:none} dialog .report-capture-note{position:static;margin:8px 0}')
                page.evaluate("""() => {document.querySelectorAll('.report-capture-note').forEach(x=>x.remove());
                  const note=document.createElement('div');note.className='report-capture-note';note.textContent='报告占位图 · 数据、画面及设备状态均为模拟';
                  (document.querySelector('dialog[open] #eventDetails')||document.body).append(note);} """)
                if whole and locator is None:
                    height=page.evaluate('Math.max(1080, document.documentElement.scrollHeight)')
                    if height>1080: page.set_viewport_size({"width":1920,"height":min(2400,height+20)})
                page.wait_for_timeout(450)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),rel
                if not keep_toast:
                    page.evaluate("document.querySelectorAll('.toast').forEach(x=>x.remove())")
                if locator:
                    box=page.locator(locator).bounding_box()
                    details=page.locator('#eventDetails').bounding_box()
                    box['height']=min(box['height'],details['y']+details['height']-box['y']+24)
                    page.screenshot(path=str(path),clip=box,animations='disabled')
                else: page.screenshot(path=str(path),full_page=True,animations='disabled')
                manifest.append(dict(file=rel,title=title,kind="模拟占位",note=note))
                print('CAPTURED',rel,flush=True)

            navigate('/', 'normal')
            shot('02_六个页面_模拟占位/01_监控总览_正常运行_模拟.png','监控总览：正常运行（备用）','优先使用真实正常总览；此图展示完整模拟环境和近期事件。')
            navigate('/history')
            shot('02_六个页面_模拟占位/02_历史趋势_烟雾曲线.png','历史趋势：烟雾','包含超过黄/红阈值后恢复的模拟趋势、统计和采样明细。')
            navigate('/alerts')
            shot('02_六个页面_模拟占位/03_告警记录_多类型事件.png','告警记录：多类型事件','包含温度、CO₂、烟雾、明火和恢复记录；邮件明确显示模拟邮件关闭。')
            navigate('/vision')
            shot('02_六个页面_模拟占位/04_明火检测_正常监测.png','明火检测：正常状态','模拟画面与模拟模型状态，用实机明火页替换。')
            navigate('/devices')
            shot('02_六个页面_模拟占位/05_设备管理_设备与服务状态.png','设备管理','数据来源为模拟，在线和邮件服务状态为界面夹具；未连接实机。')
            runtime.scenario='normal';navigate('/simulation')
            shot('02_六个页面_模拟占位/06_模拟与联调_场景操作.png','模拟与联调','正常采样场景、可选测试场景与联动状态。')

            for i,(prefix,label) in enumerate([('temp','温度'),('rh','湿度'),('co2','CO2'),('smoke','烟雾')]):
                key={'temp':'temp_c','rh':'rh','co2':'co2_ppm','smoke':'smoke_raw'}[prefix]
                for j,(suffix,level,title) in enumerate([('yellow','yellow','黄色预警'),('red','red','红色报警')]):
                    runtime.select(prefix+'_'+suffix)
                    runtime.event(key,level,runtime.sample[key],device='report-current:'+key)
                    navigate('/')
                    expect(page.locator('#riskLabel')).to_have_text(title)
                    shot(f'03_异常与联动_模拟占位/{i*2+j+1:02d}_监控总览_{label}_{title}.png',f'{label}异常：{title}',f'{label}单指标{title}，其余指标正常；实际值见仪表。')
            runtime.select('combined')
            for key in ['temp_c','co2_ppm','smoke_raw']:runtime.event(key,'red',runtime.sample[key],device='report-combined:'+key)
            navigate('/')
            shot('03_异常与联动_模拟占位/09_监控总览_多指标红色报警.png','多指标红色报警','温度、CO₂ 和烟雾同时异常，展示风险来源汇总。')
            navigate('/', 'missing')
            shot('03_异常与联动_模拟占位/10_监控总览_烟雾数据缺失.png','烟雾数据缺失','缺失读数显示未知，不用零值代替；其他指标保持有效。')
            navigate('/', 'offline')
            shot('03_异常与联动_模拟占位/11_监控总览_采集节点离线.png','采集节点离线','模拟 ESP32/MQTT 断线和全部环境指标缺失，摄像头独立可用。')
            runtime.event('smoke_raw','red',2486,age=35,device='report-recovery:smoke')
            for delta in range(0,19,2):runtime.event('smoke_raw','green',368,age=20-delta,device='report-recovery:smoke')
            navigate('/', 'recovery')
            shot('03_异常与联动_模拟占位/12_监控总览_报警后恢复正常.png','报警后恢复','风险和声光恢复正常，最新记录显示恢复事件。')
            navigate('/', 'normal')
            page.wait_for_timeout(2300)
            runtime.select('smoke_red');runtime.event('smoke_raw','red',2486,device='report-toast')
            expect(page.locator('.toast.red')).to_be_visible(timeout=8000)
            expect(page.locator('#riskLabel')).to_have_text('红色报警')
            shot('03_异常与联动_模拟占位/13_监控总览_红色报警弹窗.png','红色报警弹窗','由隔离后台新增事件触发实际前端弹窗，没有调用 SMTP 或 MQTT。',keep_toast=True)

            navigate('/history','normal')
            for i,(key,label) in enumerate([('temp_c','温度'),('rh','湿度'),('co2_ppm','CO2')],1):
                page.locator(f'[data-history-metric="{key}"]').click()
                shot(f'04_查询与详情_模拟占位/{i:02d}_历史趋势_{label}.png',f'历史趋势：{label}',f'{label}曲线、阈值和统计，数据完全模拟。')
            page.locator('[data-history-metric="smoke_raw"]').click()
            page.locator('#historyDate summary').click()
            shot('04_查询与详情_模拟占位/04_历史趋势_自选时间范围.png','历史时间范围查询','展示起止时间选择入口。',whole=False)
            page.keyboard.press('Escape')
            with page.expect_download() as download:
                page.locator('#exportHistory').click()
            export=out/'05_辅助资料/模拟历史数据导出.csv';export.parent.mkdir(exist_ok=True)
            download.value.save_as(export)
            navigate('/alerts?source=smoke')
            shot('04_查询与详情_模拟占位/05_告警记录_烟雾类别筛选.png','按烟雾类别筛选','模拟事件筛选结果。')
            page.locator('[data-level-filter="red"]').click()
            expect(page.locator('#eventRows .event-level.red').first).to_be_visible()
            shot('04_查询与详情_模拟占位/06_告警记录_红色等级筛选.png','红色报警筛选','烟雾类别与红色等级联合筛选。')
            page.locator('#eventRows button').first.click()
            expect(page.locator('#eventDialog')).to_be_visible()
            shot('04_查询与详情_模拟占位/07_告警详情_烟雾触发快照.png','烟雾告警详情','触发指标高亮、环境快照和通知结果。',whole=False)
            shot('04_查询与详情_模拟占位/08_告警详情_烟雾抽屉局部.png','烟雾事件抽屉局部','适合正文中放大展示事件快照。',locator='#eventDialog',whole=False)
            page.keyboard.press('Escape')
            navigate('/vision','fire')
            shot('04_查询与详情_模拟占位/09_明火检测_识别报警与事件.png','明火检测：报警状态','示意火焰框和置信度为模拟，保留清晰占位标记。')
            page.locator('.vision-event-detail').filter(has_text='查看详情').nth(1).click()
            # Pick a red event explicitly if ordering changes.
            if page.locator('.vision-event-image').count()==0:
                page.keyboard.press('Escape')
                page.locator('.vision-event-copy').filter(has=page.locator('.vision-event-thumbnail')).first.locator('button').click()
            expect(page.locator('.vision-event-image')).to_be_visible()
            shot('04_查询与详情_模拟占位/10_明火事件_截图详情.png','明火事件截图详情','模拟触发帧、置信度和通知信息。',whole=False)
            page.keyboard.press('Escape')
            navigate('/vision','camera_offline')
            shot('04_查询与详情_模拟占位/11_明火检测_摄像头断线.png','摄像头断线提示','无法判断明火状态，避免用无画面表示正常。')
            runtime.scenario='alarm';navigate('/simulation','smoke_red')
            shot('04_查询与详情_模拟占位/12_模拟联调_红色报警场景.png','模拟联调：红色报警','选中红色场景后的读数和反馈，未操作实际硬件。')
            navigate('/devices','offline')
            shot('04_查询与详情_模拟占位/13_设备管理_节点断线状态.png','设备管理：节点离线','模拟断线状态，设备清单仍保留。')
            assert not errors,errors
            browser.close()
    finally:
        stop.set();producer.join(2);server.shutdown()
    assert runtime.client is None and not runtime.started
    for item in manifest:
        item['sha256']=hashlib.sha256((out/item['file']).read_bytes()).hexdigest()
    (out/'图片清单.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 课程设计报告配图清单','',
           '真实截图保持原图不修改；模拟图由本项目实际页面渲染，使用独立数据库和示意画面，均有占位标记。没有连接实际 MQTT、摄像头或 SMTP。',
           '', '建议正文优先选用：真实正常总览、历史烟雾趋势、告警列表、设备管理、模拟联调、黄色预警、真实明火报警、真实告警邮件、烟雾事件详情和明火事件截图。其他图作为备选，避免报告重复堆图。',
           '', '所有模拟图片最终可按同名文件替换为实机截图。原始截图由用户提供，反映的是拍摄当时状态；真实明火截图是手机视频回放联调。',
           '', '| 文件 | 图片用途 | 来源与说明 |','| --- | --- | --- |']
    for item in manifest:lines.append(f"| [{item['file']}]({item['file']}) | {item['title']} | **{item['kind']}**：{item['note']} |")
    lines.extend(['','辅助资料中的 CSV 为模拟数据导出，不作为实测原始记录。','', '截图工具：tools/capture_report_screenshots.py。生成日期：2026-10-16。'])
    (out/'图片清单与使用说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    (out/'使用说明.txt').write_text('课程设计报告配图\n\n双击“图片总览.html”浏览全部图片。\n01_真实截图：用户原图，未经修改，优先使用。\n02_六个页面_模拟占位：补齐六页。\n03_异常与联动_模拟占位：四指标黄/红两级、多指标、缺测、离线、恢复与弹窗。\n04_查询与详情_模拟占位：趋势切换、筛选、时间范围、详情和截图等。\n05_辅助资料：模拟 CSV。\n\n模拟图页底或抽屉内有“报告占位”标记；后续用同名实机图替换。不要将模拟数值写成实测性能。真实图片未上传 GitHub。\n完整用途见“图片清单与使用说明.md”。\n',encoding='utf-8-sig')
    cards=''.join(f'<article><a href="{html.escape(i["file"])}"><img loading="lazy" src="{html.escape(i["file"])}"></a><div class="meta"><span class="tag {"real" if i["kind"]=="真实截图" else "sim"}">{i["kind"]}</span><h2>{html.escape(i["title"])}</h2><p>{html.escape(i["note"])}</p><code>{html.escape(i["file"])}</code></div></article>' for i in manifest)
    gallery='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>课程设计报告配图</title><style>body{margin:0;padding:40px;background:#f2f5f1;color:#233f34;font-family:"Microsoft YaHei",sans-serif}h1{font-size:28px;margin:0 0 12px}header>p{color:#61756a;line-height:1.8}section{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px;margin-top:28px}article{background:white;border:1px solid #d8e1d8;border-radius:10px;overflow:hidden}img{width:100%;height:255px;object-fit:contain;background:#e6ece6;display:block}.meta{padding:18px}h2{font-size:17px;margin:12px 0 8px}p{font-size:13px;line-height:1.7}.tag{padding:4px 9px;border-radius:4px;font-size:12px}.real{background:#e3f2e8;color:#236341}.sim{background:#fff2da;color:#846331}code{font-size:10px;overflow-wrap:anywhere;color:#7f8f85}@media(max-width:1000px){section{grid-template-columns:repeat(2,minmax(0,1fr))}}</style><header><h1>面向工厂的安全生产物联网监控网络 · 报告配图</h1><p>共 '+str(len(manifest))+' 张图。真实原图优先使用，模拟图保留占位标识，后续可直接替换。点击图片打开高清原图。</p></header><section>'+cards+'</section></html>'
    (out/'图片总览.html').write_text(gallery,encoding='utf-8')
    print(json.dumps(dict(status='complete',output=str(out),images=len(manifest),js_errors=errors,mqtt_connected=False,smtp_sent=False),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
