"""后台采样、MQTT、规则与邮件。所有工作与是否打开网页无关。"""
import copy
import json
import logging
import math
import threading
import time

from alert_rule import METRICS, evaluate_metric
from camera_capture import CameraCapture
from environment_store import EnvironmentStore
from mqtt_ingest import normalize_payload
from notification_core import AlertStore, MailSettings, deliver_one
import settings

LOGGER = logging.getLogger("factory-runtime")
MODES = {"hybrid": "真实环境＋模拟烟雾", "demo": "全模拟演示", "live": "全部真实采集"}
SCENARIOS = {"normal": "正常", "rising": "逐渐升高", "warning": "预警",
             "alarm": "报警", "recovery": "恢复", "disconnected": "烟雾断线"}


class MonitorRuntime:
    def __init__(self, data_dir=settings.DATA_DIR, mode=settings.MODE, mail=None, clock=time.time, camera=None):
        if mode not in MODES:
            raise ValueError("未知运行模式")
        self.clock = clock
        self.camera = camera if camera is not None else CameraCapture()
        self.env = EnvironmentStore(data_dir / "environment.sqlite3")
        self.alerts = AlertStore(data_dir / "alerts.sqlite3")
        self.mail_error = False
        try:
            self.mail = mail if mail is not None else MailSettings.load(settings.MAIL_CONFIG)
        except (ValueError, OSError):
            self.mail = MailSettings()
            self.mail_error = True
        self.lock = threading.RLock()
        self.mode = mode
        self.scenario = "normal"
        self.scenario_started = self.clock()
        self.mqtt_connected = False
        self.mqtt_detail = "等待连接"
        self.received_at = None
        self.received = {}
        self.sample = None
        self.error = None
        self.stop_event = threading.Event()
        self.threads = []
        self.client = None
        self.started = False

    def ingest(self, payload, now=None):
        normalized = normalize_payload(payload)
        with self.lock:
            self.received = normalized
            self.received_at = self.clock() if now is None else now

    def configure(self, mode=None, scenario=None):
        if mode is not None and mode not in MODES:
            raise ValueError("未知运行模式")
        if scenario is not None and scenario not in SCENARIOS:
            raise ValueError("未知烟雾场景")
        with self.lock:
            if (mode or self.mode) == "live" and scenario is not None:
                raise ValueError("真实采集模式不允许注入模拟烟雾")
            if mode is not None and mode != self.mode:
                self.mode = mode
                self.scenario = "normal"
                self.scenario_started = self.clock()
            if scenario is not None:
                self.scenario = scenario
                self.scenario_started = self.clock()
            return self.tick()

    def smoke_value(self, now):
        elapsed = max(0, now - self.scenario_started)
        if self.scenario == "disconnected":
            return None
        if self.scenario == "rising":
            return round(min(2500, 350 + elapsed * 85))
        if self.scenario == "warning":
            return round(1450 + 30 * math.sin(elapsed))
        if self.scenario == "alarm":
            return round(2500 + 40 * math.sin(elapsed))
        return round(350 + 20 * math.sin(elapsed / 4))

    def tick(self, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            fresh = self.received_at is not None and 0 <= now - self.received_at < settings.OFFLINE_SECONDS
            sample = {"ts": now, "mode": self.mode, "sources": {}, "device": "workshop1",
                      "hardware_device": self.received.get("hardware_device"), "received_at": self.received_at,
                      "esp32_online": fresh and self.mqtt_connected}
            for key in METRICS:
                if key == "smoke_raw" and self.mode != "live":
                    value, source = self.smoke_value(now), "simulation"
                elif self.mode == "demo":
                    baseline, amplitude = {"temp_c": (26, .6), "rh": (52, 1.8), "co2_ppm": (650, 30)}[key]
                    value, source = round(baseline + amplitude * math.sin(now / 15), 1), "simulation"
                else:
                    value = self.received.get(key) if fresh else None
                    source = "mqtt" if value is not None else "missing"
                sample[key] = value
                sample["sources"][key] = source
            sample["lux"] = (round(280 + 15 * math.sin(now / 18), 1) if self.mode == "demo"
                             else self.received.get("lux") if fresh else None)
            sample["co2_valid"] = sample["co2_ppm"] is not None
            sample["smoke_valid"] = sample["smoke_raw"] is not None
            sample["channels"] = {key: evaluate_metric(key, sample[key]) for key in METRICS}
            levels = [item["level"] for item in sample["channels"].values()]
            sample["level"] = next((level for level in ("red", "yellow", "gray") if level in levels), "green")
            sample["missing"] = [METRICS[key]["label"] for key in METRICS if sample[key] is None]
            self.env.append(sample)
            for key, evaluation in sample["channels"].items():
                event_data = dict(sample, device=f"workshop1:{self.mode}:{key}", metric=key,
                                  event_source="smoke" if key == "smoke_raw" else "environment",
                                  is_demo=sample["sources"][key] == "simulation")
                event = self.alerts.observe(event_data, evaluation, self.mail.enabled, now, self.mail.allow_demo)
                if event and self.client and self.mqtt_connected:
                    self.client.publish(settings.ALERT_TOPIC, json.dumps(event, ensure_ascii=False),
                                        qos=1, retain=False)
            self.alerts.heartbeat(self.mail.enabled, self.mqtt_connected)
            self.sample = sample
            # Commands echo a fresh sample token; no retained/offline command queue.
            # The firmware keeps its own local alarm active even if this asks for green.
            if fresh and self.client and self.mqtt_connected and self.received.get("alarm_token") and self.received.get("sound_light"):
                level = self.alerts.active_level(f"workshop1:{self.mode}:{key}" for key in METRICS)
                self.client.publish(settings.ALARM_CONTROL_TOPIC,
                                    f"{self.received['alarm_token']}|{level}", qos=0, retain=False)
            self.error = None
            return copy.deepcopy(sample)

    def sound_light_status(self):
        telemetry = self.received.get("sound_light")
        if not telemetry:
            return {"state": "pending", "detail": "等待声光固件上报"}
        if (not self.mqtt_connected or self.received_at is None or
                not 0 <= self.clock() - self.received_at < settings.OFFLINE_SECONDS):
            return {"state": "offline", "detail": "设备离线，声光状态未知"}
        level = telemetry["effective_level"]
        label = {"green": "待命", "yellow": "黄色预警", "red": "红色报警", "gray": "采样待恢复"}[level]
        route = "本地＋平台" if telemetry["remote_active"] else "本地控制"
        return dict(telemetry, state="ready", detail=f"{label} · {route}")

    def snapshot(self):
        with self.lock:
            sample = copy.deepcopy(self.sample)
            # A stopped worker or frozen backend must never look like fresh normal readings.
            stale = sample is not None and self.clock() - sample["ts"] >= settings.OFFLINE_SECONDS
            return {"sample": sample, "mode": self.mode, "mode_label": MODES[self.mode],
                    "scenario": self.scenario, "runtime_online": bool(sample) and not stale and self.error is None,
                    "mqtt": {"connected": self.mqtt_connected, "detail": self.mqtt_detail,
                             "topic": settings.ENV_TOPIC},
                    "mail": {"enabled": self.mail.enabled, "allow_demo": self.mail.allow_demo,
                             "config_error": self.mail_error},
                    "vision": {"state": "pending", "detail": "明火模型待接入；当前未运行视觉检测"},
                    "camera": self.camera.snapshot(),
                    "sound_light": self.sound_light_status(),
                    "error": self.error}

    def _sample_loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                LOGGER.exception("后台采样失败")
                with self.lock:
                    self.error = "后台采样暂时失败"
            self.stop_event.wait(settings.SAMPLE_SECONDS)

    def _mail_loop(self):
        while not self.stop_event.is_set():
            try:
                deliver_one(self.alerts, self.mail)
            except Exception:
                LOGGER.exception("邮件队列处理失败")
            self.stop_event.wait(1)

    def _start_mqtt(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self.mqtt_detail = "MQTT 依赖未安装；模拟功能可用"
            return
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if settings.MQTT_USERNAME:
            self.client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)

        def connected(client, userdata, flags, reason_code, properties):
            with self.lock:
                self.mqtt_connected = not reason_code.is_failure
                self.mqtt_detail = "已连接，等待采样" if self.mqtt_connected else "连接被拒绝"
            if not reason_code.is_failure:
                client.subscribe(settings.ENV_TOPIC, qos=1)

        def disconnected(client, userdata, flags, reason_code, properties):
            with self.lock:
                self.mqtt_connected = False
                self.mqtt_detail = "连接断开，后台正在重连"

        def failed(client, userdata):
            with self.lock:
                self.mqtt_connected = False
                self.mqtt_detail = "代理未连接，后台正在重试"

        def message(client, userdata, msg):
            # Retained readings may be hours old; receipt time must not make them fresh.
            if msg.retain:
                return
            try:
                self.ingest(json.loads(msg.payload.decode("utf-8")))
            except (ValueError, UnicodeDecodeError, TypeError):
                LOGGER.warning("忽略无效采样报文")

        self.client.on_connect = connected
        self.client.on_disconnect = disconnected
        self.client.on_connect_fail = failed
        self.client.on_message = message
        self.client.reconnect_delay_set(1, 30)
        self.client.connect_async(settings.MQTT_HOST, settings.MQTT_PORT, 60)
        self.client.loop_start()

    def start(self):
        with self.lock:
            if self.started:
                return
            self.started = True
        self._start_mqtt()
        self.camera.start()
        for target in (self._sample_loop, self._mail_loop):
            thread = threading.Thread(target=target, daemon=True)
            self.threads.append(thread)
            thread.start()

    def stop(self):
        self.stop_event.set()
        self.camera.stop()
        if self.client:
            self.client.disconnect()
            self.client.loop_stop()
        for thread in self.threads:
            thread.join(timeout=3)
