"""课设演示规则；阈值不代表工业安全标准。每个指标独立判定。"""
import math

METRICS = {
    "temp_c": {"label": "温度", "unit": "°C", "yellow": 35, "red": 45, "range": [-40, 125]},
    "rh": {"label": "相对湿度", "unit": "%RH", "yellow": 75, "red": 85, "range": [0, 100]},
    "co2_ppm": {"label": "CO₂", "unit": "ppm", "yellow": 1000, "red": 1500, "range": [0, 40000]},
    "smoke_raw": {"label": "烟雾相对值", "unit": "ADC", "yellow": 1200, "red": 2000, "range": [0, 4095]},
}

def safe_float(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None

def normalize_metric(key, value):
    result = safe_float(value)
    bounds = METRICS[key]["range"]
    return result if result is not None and bounds[0] <= result <= bounds[1] else None

def evaluate_metric(key, value):
    value = normalize_metric(key, value)
    rule = METRICS[key]
    if value is None:
        return {"level": "gray", "reason": f"{rule['label']}暂无有效数据", "abnormal": []}
    level = "red" if value >= rule["red"] else "yellow" if value >= rule["yellow"] else "green"
    reason = (f"{rule['label']} {value:g} {rule['unit']}，达到{'报警' if level == 'red' else '预警'}阈值"
              if level != "green" else f"{rule['label']}恢复到演示阈值内")
    return {"level": level, "reason": reason, "abnormal": [(key, value, level)] if level != "green" else []}

if __name__ == "__main__":
    print("告警规则已由 dashboard.py 的后台服务统一运行，无需单独启动此脚本。")
