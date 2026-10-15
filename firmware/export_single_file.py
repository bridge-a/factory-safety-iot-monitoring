"""Export the tested firmware as one Arduino sketch with inline configuration."""
from pathlib import Path
import argparse

ROOT = Path(__file__).resolve().parent


def render(config):
    source = (ROOT / "factory_sensors/factory_sensors.ino").read_text(encoding="utf-8")
    logic = (ROOT / "factory_sensors/alarm_logic.h").read_text(encoding="utf-8")
    logic = logic.replace("#pragma once\n", "", 1)
    # Explicit declarations avoid Arduino's prototype generator moving custom
    # namespace/type declarations ahead of the types they depend on.
    logic = logic.replace("inline const char* name(Level level) {",
                          "inline const char* name(Level level);\ninline Level maximum(Level a, Level b);\n"
                          "inline const char* name(Level level) {")
    parse_signature = ("inline bool parseCommand(const uint8_t* payload, size_t length, const char* expected,\n"
                       "                         uint32_t sampleAt, uint32_t now, Level& level)")
    logic = logic.replace(parse_signature + " {", parse_signature + ";\n" + parse_signature + " {")
    output_signature = "inline Outputs outputs(Level level, bool missing, uint32_t elapsed)"
    logic = logic.replace(output_signature + " {", output_signature + ";\n" + output_signature + " {")
    source = source.replace('#include "alarm_logic.h"', logic.rstrip(), 1)
    start = source.index('#if __has_include("config.local.h")')
    end = source.index("#endif", start) + len("#endif")
    source = source[:start] + source[end:]
    declarations = """Level localLevel();
void serviceAlarm();
void scdError(const char* operation, int16_t code);
bool startCO2();
void retrySensors();
void updateCO2();
void jsonNumber(char* output, size_t capacity, float value, bool valid);
void sampleSensors();
void onCommand(char* topic, uint8_t* payload, unsigned int length);
void networkTask(void*);
void setup();
void loop();

"""
    source = source.replace("Level localLevel() {", declarations + "Level localLevel() {", 1)
    source = source.replace("fill config.local.h", "fill Wi-Fi and MQTT settings at the top of this sketch")
    settings = "\n".join(line for line in config.splitlines() if line.startswith("constexpr "))
    intro = ("// 单文件完整固件：在下面填写 Wi-Fi 和树莓派 IP，然后编译上传。\n"
             "// 填入真实密码的版本只保存在 firmware/local/，不要上传到公开仓库。\n"
             "// 需要原有传感器库；无需 config.local.h 或 alarm_logic.h 配套文件。\n")
    return intro + source.replace("#include <Arduino.h>",
                                  "#include <Arduino.h>\n\n// ===== 网络配置与 I2C 引脚 =====\n" +
                                  settings + "\n// ===== 以下为采集和告警程序 =====\n", 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", action="store_true", help="Refresh the public sketch with empty credentials")
    args = parser.parse_args()
    if args.public:
        target = ROOT / "factory_all_in_one/factory_all_in_one.ino"
        config_path = ROOT / "factory_sensors/config.example.h"
    else:
        target = ROOT / "local/factory_all_in_one/factory_all_in_one.ino"
        config_path = ROOT / "factory_sensors/config.local.h"
        if not config_path.exists():
            config_path = ROOT / "factory_sensors/config.example.h"
        if target.exists():
            raise SystemExit("Local sketch already exists; kept it unchanged to preserve your settings.")
    text = render(config_path.read_text(encoding="utf-8"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"Created: {target}")


if __name__ == "__main__":
    main()
