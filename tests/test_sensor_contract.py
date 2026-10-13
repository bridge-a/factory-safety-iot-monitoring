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
