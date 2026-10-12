"""多页面真实浏览器验证。使用独立模拟库，不发送外部邮件。"""
import json
import logging
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from dashboard import create_app
from notification_core import MailSettings
from runtime import MonitorRuntime


def main():
    root = Path(__file__).resolve().parents[1]
    artifacts = root / "artifacts" / "browser-check"
    artifacts.mkdir(parents=True, exist_ok=True)
    report_path = artifacts / "browser-check.json"
    report_path.write_text('{"status":"running"}', encoding="utf-8")
    runtime = MonitorRuntime(artifacts / ("browser-data-" + uuid.uuid4().hex), mode="demo", mail=MailSettings())
    now = time.time()
    runtime.scenario_started = now - 600
    for index in range(100):
        runtime.tick(now - 600 + index * 6)
    runtime.tick()
    app = create_app(runtime, start_runtime=True)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    errors, checks, layouts = [], [], []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width":1920,"height":1080}, device_scale_factor=1)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            expect(page.locator("#connection")).to_have_text("后台运行中")
            expect(page.locator('[data-metric="temp_c"] [data-role="source"]')).to_have_text("模拟数据")
            expect(page.locator("#historyCount")).to_contain_text("条采样")
            page.screenshot(path=str(artifacts/"overview-1920.png"),full_page=True)
            checks.append("overview gauges and history")

            page.get_by_role("link", name="模拟与联调", exact=True).first.click()
            expect(page.locator("#simValue")).not_to_have_text("--")
            page.locator('[data-scenario="alarm"]').click()
            expect(page.locator(".toast.red")).to_have_count(1)
            expect(page.locator("#simState")).to_contain_text("红色报警")
            expect(page.locator('[data-scenario="alarm"]')).to_have_attribute("aria-pressed","true")
            expect(page.locator("#simReadingPanel")).to_have_attribute("data-level","red")
            page.get_by_role("link", name="监控总览", exact=True).click()
            expect(page.locator('[data-metric="smoke_raw"] [data-role="state"]')).to_have_text("报警")
            expect(page.locator("#recentList")).to_contain_text("烟雾相对值")
            expect(page.locator(".toast.red")).to_have_count(0)  # Navigation does not replay notifications.
            page.screenshot(path=str(artifacts/"overview-alarm-1920.png"),full_page=True)
            checks.append("scenario alarm and notification cursor across pages")

            page.get_by_role("link", name="告警记录", exact=True).click()
            expect(page.locator("#eventTotal")).to_have_text("1")
            page.locator('#eventRows button').first.click()
            expect(page.locator("#eventDialog")).to_be_visible()
            expect(page.locator("#eventDetails")).to_contain_text("模拟演示")
            page.get_by_role("button", name="关闭详情").click()
            page.locator("#alertSource").select_option("vision")
            page.get_by_role("button",name="筛选记录").click()
            expect(page.locator("#eventTotal")).to_have_text("0")
            expect(page.locator(".empty-state")).to_be_visible()
            page.get_by_role("button",name="重置",exact=True).click()
            expect(page.locator("#eventTotal")).to_have_text("1")
            page.locator(".date-disclosure summary").click()
            page.locator("#alertFrom").fill("2099-01-01T00:00")
            page.get_by_role("button",name="筛选记录").click()
            expect(page.locator("#eventTotal")).to_have_text("0")
            page.get_by_role("button",name="重置",exact=True).click()
            expect(page.locator("#eventTotal")).to_have_text("1")
            page.locator(".date-disclosure summary").click()
            page.screenshot(path=str(artifacts/"alerts-1920.png"),full_page=True)
            checks.append("event detail, filters and reset")

            page.get_by_role("link",name="历史趋势",exact=True).click()
            expect(page.locator("#statTotal")).not_to_have_text("--")
            page.get_by_role("button",name="24 小时",exact=True).click()
            expect(page.locator("#historyMessage")).to_contain_text("1440")
            page.locator("#chartMetric").select_option("temp_c")
            expect(page.locator(".statUnit").first).to_have_text("°C")
            page.locator("#historyMode").select_option("live")
            expect(page.locator("#statTotal")).to_have_text("0")
            page.locator("#historyMode").select_option("demo")
            expect(page.locator("#statTotal")).not_to_have_text("0")
            cells = page.locator("#sampleRows tr").first.locator("td").all_text_contents()
            assert 20 < float(cells[1]) < 35, cells
            assert 400 < float(cells[3]) < 900, cells
            assert float(cells[4]) > 300, cells
            with page.expect_download() as download_info:
                page.get_by_role("button",name="导出 CSV",exact=True).click()
            download = download_info.value
            download.save_as(artifacts / "history-export.csv")
            assert (artifacts/"history-export.csv").read_text(encoding="utf-8-sig").startswith("时间")
            page.screenshot(path=str(artifacts/"history-1920.png"),full_page=True)
            checks.append("history mode isolation, range, statistics and CSV download")

            page.get_by_role("link",name="模拟与联调",exact=True).first.click()
            page.locator('[data-scenario="disconnected"]').click()
            expect(page.locator("#simValue")).to_have_text("--")
            expect(page.locator("#simSamplingStatus")).to_have_text("数据待接入")
            assert len(runtime.alerts.list_events()["events"]) == 1
            page.locator('[data-scenario="recovery"]').click()
            expect(page.locator(".toast.green")).to_have_count(1,timeout=22000)
            assert runtime.alerts.list_events()["events"][0]["level"] == "green"
            page.locator("#modeSelect").select_option("live")
            expect(page.locator('[data-scenario="alarm"]')).to_be_disabled()
            expect(page.locator("#simValue")).to_have_text("--")
            page.locator("#modeSelect").select_option("demo")
            checks.append("disconnect, recovery hold and live mode injection guard")

            routes = [("overview","/"),("history","/history"),("alerts","/alerts"),("vision","/vision"),("devices","/devices"),("simulation","/simulation")]
            for width,height in [(1920,1080),(1366,768),(390,844)]:
                page.set_viewport_size({"width":width,"height":height})
                for name,route in routes:
                    page.goto(url+route)
                    expect(page.locator("#connection")).to_have_text("后台运行中")
                    if name in ("overview","history"):
                        expect(page.locator("#historyCount")).to_contain_text("条采样")
                    if name == "overview":
                        assert page.evaluate("""() => [...document.querySelectorAll('.sensor-gauge')].every(el => {
                            const time = el.querySelector('[data-role="time"]').getBoundingClientRect();
                            const panel = el.closest('.panel').getBoundingClientRect();
                            return time.bottom <= panel.bottom - 3;
                        })"""), (width, "gauge timestamp clipped")
                    if name=="alerts":
                        expect(page.locator("#eventTotal")).not_to_have_text("--")
                    layout=page.evaluate("""() => ({
                        width:innerWidth, scrollWidth:document.documentElement.scrollWidth,
                        height:document.documentElement.scrollHeight,
                        riskTop:document.getElementById('riskCard')?.getBoundingClientRect().top,
                        navCount:document.querySelectorAll('nav a').length
                    })""")
                    layouts.append({"page":name,**layout})
                    assert layout["scrollWidth"] <= width, (name,width,layout)
                    if name=="overview" and width>=1366:
                        assert layout["riskTop"] < height
                    page.screenshot(path=str(artifacts/f"{name}-{width}.png"),full_page=True)
            checks.append("all six pages at desktop, laptop and mobile sizes")

            page.close()
            runtime.configure(scenario="warning")
            last=runtime.snapshot()["sample"]["ts"]
            deadline=time.monotonic()+6
            while time.monotonic()<deadline and runtime.snapshot()["sample"]["ts"]<=last:
                time.sleep(.25)
            assert runtime.env.history(last+.001,"demo")
            checks.append("background monitoring continues without a page")

            page=browser.new_page(viewport={"width":1366,"height":768})
            page.on("pageerror",lambda error:errors.append(str(error)))
            page.goto(url)
            expect(page.locator("#connection")).to_have_text("后台运行中")
            page.route("**/api/**",lambda route:route.abort())
            expect(page.locator("#connection")).to_have_text("后台连接中断",timeout=11000)
            expect(page.locator('[data-metric="temp_c"] [data-role="value"]')).to_have_text("--")
            checks.append("offline state clears live gauges")
            browser.close()
        assert not errors, errors
        report={"status":"passed","checks":checks,"layouts":layouts,"javascript_errors":errors,"real_email_sent":False,"hardware_connected":False}
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({key:value for key,value in report.items() if key!="layouts"},ensure_ascii=False,indent=2))
    except Exception as exc:
        report_path.write_text(json.dumps({"status":"failed","error":str(exc),"checks":checks,"layouts":layouts,"javascript_errors":errors},ensure_ascii=False,indent=2),encoding="utf-8")
        raise
    finally:
        server.shutdown()
        runtime.stop()


if __name__=="__main__":
    main()
