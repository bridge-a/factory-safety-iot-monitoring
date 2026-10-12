import json
import re
from pathlib import Path
import pytest

from dashboard import create_app
from mqtt_ingest import normalize_payload
from notification_core import AlertStore, MailSettings, compose_mail, deliver_one
from runtime import MonitorRuntime


@pytest.fixture
def rig(tmp_path):
    now = [1_790_000_000.0]
    runtime = MonitorRuntime(tmp_path, mail=MailSettings(), clock=lambda: now[0])
    return runtime, now


def test_hybrid_never_fabricates_hardware_data(rig):
    runtime, now = rig
    sample = runtime.tick()
    assert sample["temp_c"] is None and sample["co2_ppm"] is None
    assert sample["smoke_raw"] > 0
    assert sample["sources"]["smoke_raw"] == "simulation"
    assert sample["level"] == "gray" and not sample["esp32_online"]
    runtime.ingest({"temp_c": 26, "rh": 52, "co2_ppm": 620, "co2_valid": True})
    sample = runtime.tick()
    assert sample["temp_c"] == 26 and sample["sources"]["co2_ppm"] == "mqtt"
    now[0] += 21
    sample = runtime.tick()
    assert sample["co2_ppm"] is None and sample["level"] == "gray"


def test_payload_rejects_fake_validity_and_invalid_numbers():
    data = normalize_payload({"temp_c": float("nan"), "rh": 110, "co2_ppm": 1500,
                              "co2_valid": "false", "smoke_raw": 2500, "smoke_valid": False})
    assert all(data[key] is None for key in ("temp_c", "rh", "co2_ppm", "smoke_raw"))
    data = normalize_payload({"temp_c": 0, "rh": 0, "co2_ppm": 0, "co2_valid": 1,
                              "smoke_raw": 0, "smoke_valid": True})
    assert all(data[key] == 0 for key in ("temp_c", "rh", "co2_ppm", "smoke_raw"))
    with pytest.raises(ValueError):
        normalize_payload([])


def test_first_alarm_dedup_escalation_and_restart(rig):
    runtime, now = rig
    runtime.configure(scenario="warning")
    assert runtime.alerts.list_events()["events"][0]["level"] == "yellow"
    for _ in range(5):
        now[0] += 2
        runtime.tick()
    assert len(runtime.alerts.list_events()["events"]) == 1
    runtime.configure(scenario="alarm")
    assert [e["level"] for e in runtime.alerts.list_events()["events"]] == ["red", "yellow"]
    runtime.alerts = AlertStore(runtime.alerts.path)
    runtime.tick()
    assert len(runtime.alerts.list_events()["events"]) == 2


def test_recovery_requires_continuous_valid_readings(rig):
    runtime, now = rig
    runtime.configure(scenario="alarm")
    runtime.configure(scenario="recovery")
    for _ in range(5):
        now[0] += 2
        runtime.tick()
    runtime.configure(scenario="disconnected")
    now[0] += 40
    runtime.tick()
    runtime.configure(scenario="recovery")
    assert len(runtime.alerts.list_events()["events"]) == 1
    for _ in range(8):
        now[0] += 2
        runtime.tick()
    events = runtime.alerts.list_events()["events"]
    assert [event["level"] for event in events] == ["green", "red"]
    assert events[0]["is_demo"]


def test_process_gap_does_not_count_as_recovery(rig):
    runtime, now = rig
    runtime.configure(scenario="alarm")
    runtime.configure(scenario="recovery")
    now[0] += 100
    runtime.alerts = AlertStore(runtime.alerts.path)
    runtime.tick()
    assert len(runtime.alerts.list_events()["events"]) == 1
    for _ in range(8):
        now[0] += 2
        runtime.tick()
    assert runtime.alerts.list_events()["events"][0]["level"] == "green"


def test_independent_channels_missing_co2_cannot_recover_alarm(rig):
    runtime, now = rig
    runtime.ingest({"temp_c": 25, "rh": 50, "co2_ppm": 1700, "co2_valid": True})
    runtime.tick()
    runtime.ingest({"temp_c": 25, "rh": 50, "co2_ppm": 600, "co2_valid": False})
    for _ in range(15):
        now[0] += 2
        runtime.tick()
    events = runtime.alerts.list_events()["events"]
    assert len(events) == 1 and events[0]["target"] == "co2_ppm" and events[0]["level"] == "red"
    runtime.configure(scenario="alarm")
    assert len(runtime.alerts.list_events()["events"]) == 2


def test_real_mode_no_simulation_and_history_separation(rig):
    runtime, now = rig
    runtime.configure(mode="demo")
    now[0] += 2
    runtime.configure(mode="live")
    assert all(runtime.sample[key] is None for key in ("temp_c", "rh", "co2_ppm", "smoke_raw"))
    with pytest.raises(ValueError):
        runtime.configure(scenario="alarm")
    runtime.ingest({"smoke_raw": 2345, "smoke_valid": True})
    runtime.tick()
    event = runtime.alerts.list_events()["events"][0]
    assert event["target"] == "smoke_raw" and not event["is_demo"]
    assert all(row["mode"] == "live" for row in runtime.env.history(0, "live"))
    assert all(row["sources"]["temp_c"] == "simulation" for row in runtime.env.history(0, "demo"))


def test_api_controls_are_validated_and_read_only_routes_work(rig):
    runtime, now = rig
    runtime.tick()
    app = create_app(runtime)
    app.testing = True
    client = app.test_client()
    html = client.get("/").text
    token = re.search(r'name="control-token" content="([^"]+)"', html).group(1)
    assert client.get("/api/status").json["sample"]["sources"]["smoke_raw"] == "simulation"
    assert client.post("/api/simulation", json={"scenario": "alarm"}).status_code == 403
    headers = {"X-Control-Token": token}
    for payload in ({"scenario": "invalid"}, {"mode": []}, {"mode": None}, {"extra": True}):
        assert client.post("/api/simulation", json=payload, headers=headers).status_code == 400
    assert client.post("/api/simulation", json={"scenario": "alarm"}, headers=headers).status_code == 200
    assert client.get("/api/notification_events?after=0").json["events"][0]["is_demo"]
    assert client.get("/api/notification_status").status_code == 200
    assert client.get("/api/history?minutes=20").status_code == 400
    assert client.get("/api/history?minutes=60").status_code == 200
    assert client.get("/api/notification_events?after=-1").status_code == 400


def test_demo_mail_blocked_by_default_and_labeled_when_enabled(tmp_path):
    mail = MailSettings(enabled=True, username="sender@qq.com", recipient="receiver@qq.com")
    runtime = MonitorRuntime(tmp_path, mail=mail)
    runtime.configure(scenario="alarm")
    assert runtime.alerts.list_events()["events"][0]["mail_status"] == "demo_blocked"
    sent = []
    assert not deliver_one(runtime.alerts, mail, sender=lambda *args: sent.append(args))
    assert not sent
    runtime.mail = MailSettings(enabled=True, allow_demo=True, username="sender@qq.com", recipient="receiver@qq.com")
    runtime.configure(mode="demo", scenario="alarm")
    assert deliver_one(runtime.alerts, runtime.mail, sender=lambda item, settings: sent.append(compose_mail(item, settings)))
    assert "模拟演示" in str(sent[0]["Subject"])
    assert runtime.alerts.list_events()["events"][0]["mail_status"] == "sent"


def test_mail_retry_survives_restart_without_exposing_smtp_text(tmp_path):
    mail = MailSettings(enabled=True, allow_demo=True, username="sender@qq.com", recipient="receiver@qq.com")
    runtime = MonitorRuntime(tmp_path, mail=mail, clock=lambda: 1000)
    runtime.configure(scenario="alarm")
    def fail(item, settings):
        raise OSError("sensitive-detail-should-not-leak")
    assert deliver_one(runtime.alerts, mail, sender=fail, now=1000)
    event = runtime.alerts.list_events()["events"][0]
    assert event["last_error"] == "OSError" and event["mail_status"] == "pending"
    store = AlertStore(runtime.alerts.path)
    assert not deliver_one(store, mail, sender=lambda *args: None, now=1029)
    assert deliver_one(store, mail, sender=lambda *args: None, now=1030)
    assert store.list_events()["events"][0]["mail_status"] == "sent"
    assert not deliver_one(store, mail, sender=lambda *args: None, now=1100)


def test_independent_pages_have_active_navigation(rig):
    runtime, now = rig
    client = create_app(runtime).test_client()
    for route, title in (("/", "监控总览"), ("/history", "历史趋势"), ("/alerts", "告警记录"),
                         ("/vision", "明火检测"), ("/devices", "设备管理"), ("/simulation", "模拟与联调")):
        response = client.get(route)
        assert response.status_code == 200
        assert title in response.text and 'aria-current="page"' in response.text


def test_history_dates_statistics_and_csv_preserve_invalid_values(rig):
    import csv
    import io
    runtime, now = rig
    start = now[0]
    runtime.configure(mode="demo")
    now[0] += 2
    runtime.configure(scenario="alarm")
    now[0] += 2
    runtime.configure(scenario="disconnected")
    runtime.configure(mode="hybrid")
    client = create_app(runtime).test_client()
    query = f"mode=demo&from={start-1}&to={now[0]+1}"
    result = client.get("/api/history?" + query).json
    assert result["statistics"]["total"] == 3
    assert result["statistics"]["metrics"]["smoke_raw"]["valid"] == 2
    assert result["statistics"]["metrics"]["smoke_raw"]["max"] >= 2000
    assert all(row["mode"] == "demo" for row in result["points"])
    exported = client.get("/api/history/export?" + query)
    rows = list(csv.reader(io.StringIO(exported.data.decode("utf-8-sig"))))
    assert len(rows) == 4
    assert rows[-1][5] == ""  # Invalid smoke is an empty field, never zero.
    assert rows[-1][9] == "simulation"
    for invalid in ("from=nan&to=200", "from=10&to=9", "from=0&to=86401", "from=100"):
        assert client.get("/api/history?" + invalid).status_code == 400
        assert client.get("/api/history/export?" + invalid).status_code == 400


def test_alert_query_filters_and_pagination(rig):
    runtime, now = rig
    runtime.configure(mode="demo", scenario="warning")
    runtime.configure(scenario="alarm")
    runtime.ingest({"co2_ppm": 2000, "co2_valid": True})
    runtime.configure(mode="hybrid")
    client = create_app(runtime).test_client()
    result = client.get("/api/alerts?mode=demo&level=red&source=smoke&q=烟雾").json
    assert result["total"] == 1 and result["events"][0]["level"] == "red"
    assert result["counts"] == {"red": 1}
    assert result["level_counts"] == {"red": 1, "yellow": 1}
    assert result["scope_total"] == 2
    assert client.get("/api/alerts?source=vision").json["total"] == 0
    assert client.get("/api/alerts?mode=hybrid&source=environment").json["events"][0]["is_demo"] is False
    # Create genuinely different channel states to exercise page boundaries.
    for index in range(25):
        runtime.alerts.observe({"device": f"extra-{index}", "mode": "demo"},
                               {"level": "red", "reason": "分页验证", "abnormal": [("smoke_raw", 2200, "red")]})
    page1 = client.get("/api/alerts?q=分页验证").json
    page2 = client.get("/api/alerts?q=分页验证&page=2").json
    assert page1["total"] == 25 and len(page1["events"]) == 20 and len(page2["events"]) == 5
    assert not {e["id"] for e in page1["events"]} & {e["id"] for e in page2["events"]}
    for invalid in ("page=0", "level=oops", "from=nan", "from=20&to=10"):
        assert client.get("/api/alerts?" + invalid).status_code == 400


def test_alert_tab_counts_keep_all_other_filters_and_ignore_level_and_page(rig):
    runtime, now = rig
    start = now[0]
    for index, (mode, source, level, reason, offset) in enumerate([
        ("demo", "smoke", "red", "烟雾测试", 1),
        ("demo", "smoke", "yellow", "烟雾测试", 2),
        ("demo", "smoke", "green", "烟雾测试恢复", 3),
        ("demo", "smoke", "red", "其他内容", 4),
        ("demo", "environment", "red", "烟雾测试", 5),
        ("live", "smoke", "red", "烟雾测试", 6),
        ("demo", "smoke", "red", "烟雾测试", 20),
    ]):
        data = {"device":f"counts-{index}", "mode":mode, "event_source":source, "metric":"smoke_raw"}
        if level == "green":
            # A recovery record must follow an actual abnormal state.
            runtime.alerts.observe(data, {"level":"red", "reason":"之前的报警", "abnormal":[("smoke_raw",2500,"red")]}, now=start-20)
            for tick in range(-13, 4, 2):
                runtime.alerts.observe(data, {"level":"green", "reason":reason, "abnormal":[]}, now=start+tick)
        else:
            runtime.alerts.observe(data, {"level":level, "reason":reason, "abnormal":[("smoke_raw",2500,level)]}, now=start+offset)
    client = create_app(runtime).test_client()
    query = f"mode=demo&source=smoke&q=烟雾测试&from={start}&to={start+10}"
    for level in ("", "red", "yellow", "green"):
        result = client.get("/api/alerts?" + query + "&level=" + level + "&page=99").json
        assert result["level_counts"] == {"red":1,"yellow":1,"green":1}
        assert result["scope_total"] == 3
        assert result["total"] == (1 if level else 3)
        assert result["page"] == 1
        if level:
            assert all(e["level"] == level for e in result["events"])
    no_red = client.get(f"/api/alerts?mode=demo&level=red&from={start+2}&to={start+2.5}").json
    assert no_red["total"] == 0 and no_red["events"] == []
    assert no_red["scope_total"] == 1 and no_red["level_counts"] == {"yellow":1}
    empty = client.get(f"/api/alerts?mode=demo&q=不存在的内容&from={start}&to={start+10}").json
    assert empty["scope_total"] == empty["total"] == 0 and empty["level_counts"] == {}
