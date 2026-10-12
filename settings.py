"""工厂课设独立的路径、端口和 MQTT 配置。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("FACTORY_DATA_DIR", BASE_DIR / "data"))
ENV_DB = str(DATA_DIR / "environment.sqlite3")
ALERT_DB = str(DATA_DIR / "alerts.sqlite3")
MQTT_HOST = os.environ.get("FACTORY_MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("FACTORY_MQTT_PORT", "1883"))
ENV_TOPIC = os.environ.get("FACTORY_ENV_TOPIC", "factory/workshop1/env")
ALERT_TOPIC = os.environ.get("FACTORY_ALERT_TOPIC", "factory/workshop1/alert")
WEB_PORT = int(os.environ.get("FACTORY_WEB_PORT", "5001"))
WEB_HOST = os.environ.get("FACTORY_WEB_HOST", "127.0.0.1")
MODE = os.environ.get("FACTORY_MODE", "hybrid")
OFFLINE_SECONDS = 20
SAMPLE_SECONDS = 2
MAIL_CONFIG = Path(os.environ.get("FACTORY_MAIL_CONFIG", BASE_DIR / "mail.ini"))
