"""Stage-one sensor contract; no physical sensors, external broker or SMTP."""
import json
from types import SimpleNamespace

import pytest
import paho.mqtt.client as mqtt

from dashboard import create_app
from notification_core import MailSettings
from runtime import MonitorRuntime
import settings


def sensor_packet(**changes):
    packet = {
        "device": "esp32-s3-workshop1", "uptime_ms": 6000,
        "temp_c": 26.25, "temp_c_valid": True,
        "rh": 61.4, "rh_valid": True, "lux": 280.0,
        "co2_ppm": 650, "co2_valid": True,
        "smoke_raw": None, "smoke_valid": False,
    }
    packet.update(changes)
    return packet


@pytest.fixture
def receiver(tmp_path, monkeypatch):
    # Use the real paho callback wiring, replacing only network operations.
    monkeypatch.setattr(mqtt.Client, "connect_async", lambda *a, **k: None)
    monkeypatch.setattr(mqtt.Client, "loop_start", lambda *a, **k: None)
    monkeypatch.setattr(settings, "MQTT_USERNAME", "")
    monkeypatch.setattr(settings, "MQTT_PASSWORD", "")
    now = [1_790_000_000.0]
    runtime = MonitorRuntime(tmp_path, mode="hybrid", mail=MailSettings(), clock=lambda: now[0])
    runtime._start_mqtt()
    runtime.mqtt_connected = True
    return runtime, now


def receive(runtime, packet, retained=False):
    runtime.client.on_message(runtime.client, None, SimpleNamespace(
        retain=retained, payload=json.dumps(packet, allow_nan=False).encode("utf-8")))


def test_sensor_report_reaches_api_history_without_humidity_scaling(receiver):
    runtime, now = receiver
    receive(runtime, sensor_packet())
    runtime.tick()
    web = create_app(runtime).test_client()
    sample = web.get("/api/status").json["sample"]
    assert sample["esp32_online"] and sample["hardware_device"] == "esp32-s3-workshop1"
    assert sample["rh"] == 61.4 and sample["lux"] == 280
    assert sample["received_at"] == now[0]  # Never ESP32 uptime.
    assert sample["sources"] == dict(temp_c="mqtt", rh="mqtt", co2_ppm="mqtt", smoke_raw="simulation")
    history = web.get(f"/api/history?from={now[0]-1}&to={now[0]+1}&mode=hybrid").json
    assert history["points"][-1]["rh"] == 61.4
    runtime.configure(mode="live")
    assert runtime.sample["smoke_raw"] is None
    assert runtime.sample["sources"]["smoke_raw"] == "missing"


def test_warmup_and_sensor_failure_remain_missing_while_node_reports(receiver):
    runtime, now = receiver
    receive(runtime, sensor_packet(co2_ppm=None, co2_valid=False, lux=None))
    runtime.tick()
    assert runtime.sample["esp32_online"]
    assert runtime.sample["co2_ppm"] is None and runtime.sample["lux"] is None
    receive(runtime, sensor_packet(temp_c=None, temp_c_valid=False, rh=None, rh_valid=False))
    runtime.tick()
    assert runtime.sample["temp_c"] is None and runtime.sample["rh"] is None
    assert runtime.sample["co2_ppm"] == 650


def test_expired_co2_cannot_recover_alarm_with_other_fresh_sensors(receiver):
    runtime, now = receiver
    receive(runtime, sensor_packet(co2_ppm=1800))
    runtime.tick()
    for _ in range(12):
        now[0] += 2
        receive(runtime, sensor_packet(co2_ppm=None, co2_valid=False))
        runtime.tick()
    assert runtime.sample["esp32_online"] and runtime.sample["co2_ppm"] is None
    events = runtime.alerts.list_events()["events"]
    assert len(events) == 1 and events[0]["level"] == "red" and not events[0]["is_demo"]
    for _ in range(9):
        now[0] += 2
        receive(runtime, sensor_packet())
        runtime.tick()
    assert [e["level"] for e in runtime.alerts.list_events()["events"]] == ["green", "red"]


def test_retained_reports_do_not_revive_disconnected_node(receiver):
    runtime, now = receiver
    receive(runtime, sensor_packet(), retained=True)
    assert runtime.received_at is None
    receive(runtime, sensor_packet())
    runtime.tick()
    now[0] += settings.OFFLINE_SECONDS + 1
    receive(runtime, sensor_packet(), retained=True)
    runtime.tick()
    assert not runtime.sample["esp32_online"]
    assert all(runtime.sample[key] is None for key in ("temp_c", "rh", "co2_ppm", "lux"))
    assert runtime.sample["sources"]["smoke_raw"] == "simulation"
    receive(runtime, sensor_packet())
    runtime.tick()
    assert runtime.sample["esp32_online"] and runtime.sample["co2_ppm"] == 650


def test_optional_broker_credentials_are_not_exposed_in_status(receiver, monkeypatch):
    runtime, _ = receiver
    monkeypatch.setattr(settings, "MQTT_USERNAME", "test-node")
    monkeypatch.setattr(settings, "MQTT_PASSWORD", "test-only-secret")
    configured = []
    monkeypatch.setattr(mqtt.Client, "username_pw_set", lambda self, user, password: configured.append((user, password)))
    runtime._start_mqtt()
    assert configured == [("test-node", "test-only-secret")]
    payload = create_app(runtime).test_client().get("/api/status").text
    assert "test-node" not in payload and "test-only-secret" not in payload


TOKEN = "1234567890abcdef-00000001"


def integrated_packet(**changes):
    return sensor_packet(smoke_raw=220, smoke_valid=True, alarm_token=TOKEN,
                         sound_light={"enabled": True, "local_level": "green", "effective_level": "green",
                                      "remote_active": False, "led_on": False, "buzzer_on": False,
                                      "command_token": ""}, **changes)


def capture_commands(runtime, monkeypatch):
    published = []
    monkeypatch.setattr(runtime.client, "publish", lambda topic, payload, **kwargs:
                        published.append((topic, payload, kwargs)))
    return published


def test_integrated_live_readings_and_firmware_status_reach_web(receiver):
    runtime, now = receiver
    receive(runtime, integrated_packet())
    runtime.configure(mode="live")
    result = create_app(runtime).test_client().get("/api/status").json
    assert result["sample"]["smoke_raw"] == 220
    assert all(source == "mqtt" for source in result["sample"]["sources"].values())
    assert result["sound_light"]["state"] == "ready"
    assert result["sound_light"]["detail"] == "待命 · 本地控制"
    runtime.mqtt_connected = False
    assert runtime.snapshot()["sound_light"]["state"] == "offline"
    runtime.mqtt_connected = True
    now[0] += 21
    assert runtime.snapshot()["sound_light"]["state"] == "offline"


def test_simulation_commands_hold_until_continuous_recovery(receiver, monkeypatch):
    runtime, now = receiver
    sent = capture_commands(runtime, monkeypatch)
    receive(runtime, integrated_packet())
    runtime.configure(scenario="alarm")
    assert sent[-1] == (settings.ALARM_CONTROL_TOPIC, TOKEN + "|red", {"qos": 0, "retain": False})
    runtime.configure(scenario="recovery")
    assert sent[-1][1].endswith("|red")
    for _ in range(8):
        now[0] += 2
        receive(runtime, integrated_packet())
        runtime.tick()
    assert sent[-1][1].endswith("|green")
    assert runtime.alerts.list_events()["events"][0]["level"] == "green"


def test_missing_channel_does_not_send_false_remote_recovery(receiver, monkeypatch):
    runtime, now = receiver
    sent = capture_commands(runtime, monkeypatch)
    receive(runtime, integrated_packet(co2_ppm=1700))
    runtime.configure(mode="live")
    for _ in range(12):
        now[0] += 2
        receive(runtime, integrated_packet(co2_ppm=None, co2_valid=False))
        runtime.tick()
    assert sent[-1][1].endswith("|red")


def test_no_command_to_legacy_invalid_retained_or_offline_node(receiver, monkeypatch):
    runtime, now = receiver
    sent = capture_commands(runtime, monkeypatch)
    receive(runtime, integrated_packet(), retained=True)
    runtime.configure(scenario="alarm")
    assert not [item for item in sent if item[0] == settings.ALARM_CONTROL_TOPIC]
    for packet in (sensor_packet(), dict(integrated_packet(), alarm_token="bad|red"),
                   dict(integrated_packet(), sound_light={"enabled": "true"})):
        receive(runtime, packet)
        runtime.tick()
    assert not [item for item in sent if item[0] == settings.ALARM_CONTROL_TOPIC]
    receive(runtime, integrated_packet())
    runtime.tick()
    count = len(sent)
    now[0] += 21
    runtime.tick()
    assert len(sent) == count
    receive(runtime, integrated_packet())
    runtime.mqtt_connected = False
    runtime.tick()
    assert len(sent) == count


def test_reported_local_alarm_is_not_overwritten_by_normal_dashboard(receiver):
    runtime, _ = receiver
    packet = integrated_packet()
    packet["sound_light"].update(local_level="red", effective_level="red", remote_active=True,
                                command_token=TOKEN, led_on=True, buzzer_on=True)
    receive(runtime, packet)
    runtime.configure(scenario="normal")
    state = runtime.snapshot()["sound_light"]
    assert state["effective_level"] == "red" and state["remote_active"]
    assert state["command_token"] == TOKEN


def test_firmware_and_backend_thresholds_match():
    import re
    from pathlib import Path
    from alert_rule import METRICS
    header = (Path(__file__).parents[1] / "firmware/factory_sensors/alarm_logic.h").read_text()
    table = re.search(r"RULES\[\]\s*=\s*\{(.*?)\};", header, re.S).group(1)
    rows = [tuple(map(float, row.split(","))) for row in re.findall(r"\{([^{}]+)\}", table)]
    assert rows == [(*rule["range"], rule["yellow"], rule["red"]) for rule in METRICS.values()]
