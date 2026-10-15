# ESP32-S3 环境采集固件

本阶段读取 SHT31、BH1750、SCD41，发送到工厂项目的 MQTT 主题。MQ-2、蜂鸣器和 LED 尚未接入本固件，烟雾由网页后台的 `hybrid` 模式模拟。原文物项目的草图保持原样。

MQ-2 已到货并接线后，可先运行独立的 [四传感器串口测试](sensor_check/README.md)，同时检查三路 I²C 传感器和 GPIO4 的 MQ-2 模拟读数。测试程序不联网；`factory_sensors/` 的正式 MQTT 固件暂未加入 MQ-2，等待实测确认后再集成。

## 接线与设备

沿用已找到的旧项目源码中的 I²C 接线；烧录前仍需核对实物板型与接线。

| 项目 | 设置 |
| --- | --- |
| 控制器 | ESP32-S3；编译基线为 Arduino 的 ESP32S3 Dev Module |
| SDA / SCL | GPIO 8 / GPIO 9，100 kHz |
| SHT31 | `0x44`，温度与相对湿度 |
| BH1750 | `0x23`，光照 |
| SCD41 | `0x62`，CO₂；周期测量 |
| 串口 | 115200 |
| MQTT 主题 | `factory/workshop1/env`，非 retained |
| 采样与上报 | 每 2 秒采样；联网时发送最新一份 |

这张表不替代各模块的供电规格。此次不变动原有传感器供电，也不设置 MQ-2 或声光器件引脚。

## 配置与编译

1. 打开 `factory_sensors/factory_sensors.ino`。
2. 复制同目录的 `config.example.h` 为 `config.local.h`，本地填写 Wi-Fi 名称、密码和树莓派局域网 IPv4 地址。需要时填写 MQTT 账号；端口与后台一致。
3. 板型选择 ESP32S3 Dev Module。Flash、PSRAM、USB CDC、上传方式和串口需按具体开发板确定，不能把通用编译参数直接当成实物设置。
4. 先验证编译，开发板接入并确认后再上传。上传到同一块 ESP32 会替换板上程序，但不会修改旧项目源码；需要回到旧演示时重新烧录旧草图。

没有 `config.local.h` 时，程序使用空配置，可以编译并输出串口采样，网络功能关闭。`config.local.h`、编译产物、Wi-Fi 密码均不提交到 GitHub。

使用本机已有依赖作为编译基线，不升级旧项目使用的库：

| 依赖 | 本机版本 |
| --- | --- |
| Arduino CLI | 1.1.1 |
| esp32 Arduino core | 3.0.7 |
| Adafruit SHT31 Library | 2.2.2 |
| Adafruit BusIO | 1.17.4 |
| BH1750 | 1.3.0 |
| PubSubClient | 2.8 |
| Sensirion I2C SCD4x | 1.1.0 |
| Sensirion Core | 0.7.3 |

若 `arduino-cli` 已在 PATH 中，可从项目根目录执行：

```powershell
arduino-cli compile --fqbn esp32:esp32:esp32s3 --build-path artifacts/firmware-build firmware/factory_sensors
```

命令只编译，不烧录。实际验证记录见 [硬件接入准备](../docs/HARDWARE_INTEGRATION.md)。

## 有效性与重连

- SHT31 输出原始物理量，不进行湿度减半。读取失败、非有限数值或越界值用 `null` 和 `*_valid=false` 表示。
- SCD41 的周期测量约 5 秒产生新值。首次有效读数之前 CO₂ 为 `null`；最近成功读数最多保留 15 秒，通信错误或无效测量立即失效。连续收不到新测量时会重新初始化，不能把旧 CO₂ 一直当成新值上报。
- BH1750 读取失败时输出 `lux=null`。失败传感器每 30 秒尝试重新初始化。
- 网络使用独立 FreeRTOS 任务。MQTT 连接失败不会让采样任务等待；连接尝试设置超时，Wi-Fi / MQTT 分别间隔重试。
- 队列只保留最新一份样本；超过 6 秒的待发送包丢弃。断网恢复后不回放历史样本。
- `uptime_ms` 只表示开机时间，后台使用接收时间入库。所有毫秒间隔使用无符号差值，兼容计时器回绕。
- 本阶段始终发送 `smoke_raw=null, smoke_valid=false`；模拟烟雾只在后台生成，并保留来源标签。

这里描述的是代码行为。传感器恢复、网络重连和长时间运行仍需实机验收。

## 接口依据

- [Sensirion SCD4x 官方 Arduino 示例](https://github.com/Sensirion/arduino-i2c-scd4x/blob/master/examples/exampleUsage/exampleUsage.ino)：测量流程与库接口。
- [PubSubClient API](https://pubsubclient.knolleary.net/api)：缓冲区、连接超时与发布接口。
- [Arduino CLI compile](https://docs.arduino.cc/arduino-cli/commands-reference/arduino-cli_compile)：独立编译命令。

这些库由 Arduino 库管理器安装；仓库不附带其源码或工具链。
