"""MQTT 输入校验。连接由统一后台服务管理，不再另起写库进程。"""
import re
from alert_rule import METRICS, normalize_metric, safe_float


def alarm_token(value):
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{16}-[0-9a-f]{8}", value) else None


def normalize_sound_light(value):
    if not isinstance(value, dict) or value.get("enabled") is not True:
        return None
    levels = {"green", "yellow", "red", "gray"}
    if any(not isinstance(value.get(key), str) or value[key] not in levels
           for key in ("local_level", "effective_level")):
        return None
    if any(type(value.get(key)) is not bool for key in ("remote_active", "led_on", "buzzer_on")):
        return None
    return {"enabled": True, "local_level": value["local_level"],
            "effective_level": value["effective_level"], "remote_active": value["remote_active"],
            "led_on": value["led_on"], "buzzer_on": value["buzzer_on"],
            "command_token": alarm_token(value.get("command_token"))}

def normalize_payload(data):
    if not isinstance(data, dict):
        raise ValueError("采样必须是 JSON 对象")
    result = {"hardware_device": str(data.get("device") or "esp32-s3")[:128]}
    for key in METRICS:
        valid_key = {"co2_ppm": "co2_valid", "smoke_raw": "smoke_valid"}.get(key, key + "_valid")
        flag = data.get(valid_key, key not in {"co2_ppm", "smoke_raw"})
        valid = flag is True or (type(flag) is int and flag == 1)
        result[key] = normalize_metric(key, data.get(key)) if valid else None
    lux = safe_float(data.get("lux"))
    result["lux"] = lux if lux is not None and lux >= 0 else None
    result["alarm_token"] = alarm_token(data.get("alarm_token"))
    result["sound_light"] = normalize_sound_light(data.get("sound_light"))
    return result

if __name__ == "__main__":
    print("MQTT 采集已由 dashboard.py 的后台服务统一运行，无需单独启动此脚本。")
