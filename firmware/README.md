# ESP32-S3 完整采集与声光告警固件

`factory_sensors/` 包含 SHT31、BH1750、SCD41、MQ-2 采集、本地阈值告警、Wi-Fi / MQTT 上报和平台声光控制。原文物项目独立保留。用户已提供前三类传感器的有效串口读数、MQ-2 ADC 读数，并确认 GPIO5 LED / GPIO6 蜂鸣器独立测试能同时开关；整合固件现已上传并完成树莓派 MQTT 联调，用户确认环境读数、平台声光响应及恢复正常，详见 [验证记录](../docs/VALIDATION.md)。

## 打开、配置与上传

推荐使用 [单文件完整程序](factory_all_in_one/factory_all_in_one.ino)：网络配置、告警逻辑和采集程序都在同一个 `.ino` 内，不需要配套配置头文件。仍需安装下方列出的传感器库。

本机已生成 `firmware/local/factory_all_in_one/factory_all_in_one.ino`，直接用 Arduino IDE 打开它，在顶部填写 Wi-Fi 名称、密码、树莓派局域网 IPv4 地址：

```cpp
constexpr char WIFI_SSID[] = "你的2.4GHz Wi-Fi名称";
constexpr char WIFI_PASSWORD[] = "你的Wi-Fi密码";
constexpr char MQTT_HOST[] = "树莓派的局域网IPv4地址";
constexpr uint16_t MQTT_PORT = 1883;
```

沿用树莓派已有 Mosquitto。ESP32 的地址不能填 `localhost`；同机运行的树莓派后台可使用 `127.0.0.1`。代理启用认证时填写 MQTT 用户名和密码。`firmware/local/` 整个目录已被 Git 忽略；GitHub 中的单文件模板保持空配置。空配置仍可编译、串口采样和本地告警，网络关闭。

新克隆仓库可执行 `python firmware/export_single_file.py` 创建本地单文件；已有本地文件时脚本拒绝覆盖，保留用户填写的内容。发布模板通过 `python firmware/export_single_file.py --public` 从 `factory_sensors/` 维护版导出，避免手工维护两套告警逻辑。维护版保留原有配置头文件方式，供代码维护与逻辑测试使用。

选择 ESP32S3 Dev Module，串口 115200；端口、Flash / PSRAM、USB CDC 沿用已经成功上传测试程序的设置。本次编译参数为 `esp32:esp32:esp32s3:CDCOnBoot=cdc`。上传替换板上测试程序，不改动旧项目文件。

## 接线保持不变

| 器件 | ESP32-S3 接线 / 设置 |
| --- | --- |
| SHT31 / BH1750 / SCD41 | SDA GPIO8，SCL GPIO9，100 kHz；地址 `0x44` / `0x23` / `0x62` |
| MQ-2 | VCC 接 5V，共地；AO 经原有 10kΩ / 10kΩ 分压接 GPIO4；DO 不接 |
| LED | GPIO5 经限流电阻接正极，负极接 GND；高电平亮 |
| 有源蜂鸣器 | GPIO6 高电平响、低电平停；沿用用户已测试接法 |

MQ-2 的 AO 不能直接把 5V 电平送入 GPIO。TMB12A05 直接接 GPIO6 的开关动作已由用户确认，但负载电流和长期可靠性未测；长期运行应补驱动电路。

## 采集与重连

- 每 2 秒采样上报，温湿度不缩放；光照作为辅助信息，不参与告警。
- SCD41 约 5 秒产生新值；首次未就绪、通信失败或超过 15 秒无有效测量时上报 `null`。失败器件每 30 秒尝试初始化。
- MQ-2 每轮取 32 次 ADC 平均值，并上报极值。`smoke_raw` 是 0～4095 相对 ADC 值，**不是 ppm**；`smoke_valid=true` 仅表示获得 ADC 样本，不能证明连通、预热或烟雾响应合格。
- 网络独立运行，Wi-Fi / MQTT 自动重连；断网不停止本地判断。队列只保留最新样本，不补发超过 6 秒的旧包。
- 声光用 `millis()` 切换，不使用两秒阻塞循环；传感器驱动初始化可能带来短暂时序抖动。

## 告警规则

| 指标 | 黄色预警 | 红色报警 |
| --- | --- | --- |
| 温度 | ≥35 °C | ≥45 °C |
| 相对湿度 | ≥75 %RH | ≥85 %RH |
| CO₂ | ≥1000 ppm | ≥1500 ppm |
| MQ-2 相对值 | ≥1200 ADC | ≥2000 ADC |

课程演示阈值尚需实测调整。后台 `alert_rule.py` 与固件 `alarm_logic.h` 应同步修改，测试检查两端阈值一致。

| 状态 | LED | 蜂鸣器 |
| --- | --- | --- |
| 全部有效且正常 | 熄灭 | 停止 |
| 黄色预警 | 亮 1 秒 / 灭 1 秒 | 每 4 秒响 0.2 秒 |
| 红色报警 | 亮 0.5 秒 / 灭 0.5 秒 | 同步响 0.5 秒 / 停 0.5 秒 |
| 核心读数缺失且无既有报警 | 每 2 秒短闪 0.1 秒 | 停止 |

升级立即生效，降级或恢复需连续 15 秒有效读数。异常指标读数缺失时保留其报警状态，不能用缺失解除告警。

## 网页声光联动

- 采样：`factory/workshop1/env`，非 retained JSON。
- 指令：`factory/workshop1/control/alarm`，由后台发布，非 retained。
- 事件：`factory/workshop1/alert`，与控制主题分开。

后台回送最新样本的 `alarm_token` 和等级，格式 `<token>|red`、`<token>|yellow` 或 `<token>|green`。ESP32 仅接受匹配最近发布样本、样本年龄小于 6 秒的指令。启动标识与序号用于拒绝旧指令，不代替 MQTT 认证。平台指令 10 秒未更新即失效。

最终输出取**本地与平台的较高等级**，平台正常不会压掉本地异常。`sound_light` 上报控制等级、GPIO 电平和最近接受的指令标识，设备页和联调页显示固件反馈；这不是实际发光、发声的电气检测。

**新固件在线时，网页 `demo` / `hybrid` 的预警、报警场景会驱动真实声光，并生成事件。** `live` 使用四项真实数据，禁用模拟注入。恢复测试需等待 15 秒；本地 CO₂ 等仍超标时，声光不会被平台强制关闭。

## 首次整体验收

1. 串口 115200：出现启动信息、每 2 秒 JSON，SCD41 从初始空值变为有效读数。
2. 网络：出现 `Wi-Fi connected`、`MQTT connected; alarm control subscribed`、持续 `PUB OK`。
3. 树莓派按 [部署与联调步骤](../docs/HARDWARE_INTEGRATION.md) 启动新版后台，`live` 下检查真实采集、历史及声光状态。
4. 临时切 `hybrid`，依次应用正常采样、黄色预警、红色报警、恢复测试，观察声光。结束后切回 `live`。
5. 断开 ESP32 网络，采样应继续；平台指令约 10 秒后失效，本地真实异常仍应报警。不要停止与原项目共用的 Mosquitto 做测试。

QQ 邮件默认未启用，另按本地配置联调。编译通过不等于硬件、网络与邮件验收。

## 依赖与复现

本机依赖：Arduino CLI 1.1.1、esp32 core 3.0.7、Adafruit SHT31 2.2.2、Adafruit BusIO 1.17.4、BH1750 1.3.0、PubSubClient 2.8、Sensirion I2C SCD4x 1.1.0、Sensirion Core 0.7.3。无需新增 ArduinoJson 库。

```powershell
arduino-cli compile --fqbn esp32:esp32:esp32s3:CDCOnBoot=cdc --build-path artifacts/integrated-firmware-build firmware/local/factory_all_in_one
g++ -std=c++11 -Wall -Wextra -pedantic tests/firmware_alarm_check.cpp -o artifacts/firmware-alarm-check.exe
.\artifacts\firmware-alarm-check.exe
```

编译产物不提交。独立诊断：[传感器测试](sensor_check/README.md)、[声光开关测试](alarm_check/README.md)。验证范围见 [验证记录](../docs/VALIDATION.md)。

接口依据：[Espressif ADC](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/adc.html)、[Sensirion 示例](https://github.com/Sensirion/arduino-i2c-scd4x/blob/master/examples/exampleUsage/exampleUsage.ino)、[PubSubClient API](https://pubsubclient.knolleary.net/api)。
