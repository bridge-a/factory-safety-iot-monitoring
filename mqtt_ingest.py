"""MQTT 输入校验。连接由统一后台服务管理，不再另起写库进程。"""
from alert_rule import METRICS, normalize_metric, safe_float

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
    return result

if __name__ == "__main__":
    print("MQTT 采集已由 dashboard.py 的后台服务统一运行，无需单独启动此脚本。")
