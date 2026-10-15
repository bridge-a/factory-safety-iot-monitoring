# 四传感器串口测试

打开同目录 `sensor_check.ino`，检查 SHT31、BH1750、SCD41 和新接入的 MQ-2。独立运行，不需要填写 Wi-Fi / MQTT 信息，不向网页写入数据。原文物源码及工厂联网固件均保留；上传此测试程序会临时替换板上运行的程序。

## 接线前提

| 信号 | ESP32-S3 接法 |
| --- | --- |
| 三个 I²C 传感器 SDA | GPIO8 |
| 三个 I²C 传感器 SCL | GPIO9 |
| MQ-2 AO | 经两只 10kΩ 电阻分压后的中点接 GPIO4 |
| MQ-2 VCC / GND | 稳定 5V / 与 ESP32 共地 |
| MQ-2 DO | 不接 |

确认 AO 没有直接把 5V 信号接入 GPIO4。分压接法为 `AO → 10kΩ → GPIO4连接点 → 10kΩ → GND`。两只相等电阻将电压约减半；程序的 `AO_est` 仅按这个比例反推。供电与分压应先用万用表核对，软件读数不能代替电气检查。

## 上传和观察

1. 在 Arduino IDE 打开 `sensor_check.ino`。选择之前成功上传的 ESP32-S3 板型及 Flash / PSRAM 参数；本机编译使用 ESP32S3 Dev Module。
2. 选择开发板对应的串口。2026-10-15 本机识别到 `COM3`，USB VID/PID 为 `303A:1001`，Arduino 将其识别为 ESP32 Family Device；重新插拔后端口可能变化，该识别不能确定具体板型。
3. 原生 USB 串口通常需在工具菜单开启 `USB CDC On Boot = Enabled`；USB-UART 转接芯片对应的接口则沿用原有 UART 设置。不要为此随意更改 Flash / PSRAM 参数。
4. 上传后打开串口监视器，波特率选择 **115200**。若没看到启动扫描，按一次开发板的 RESET / EN 键。
5. 程序每约 2 秒输出一组读数，失败的 I²C 传感器每 30 秒尝试重新初始化。截取启动扫描和连续几组读数供核对。

依赖与现有项目相同：Adafruit SHT31 Library、Adafruit BusIO、BH1750、Sensirion I2C SCD4x、Sensirion Core。`Wire` 与 ADC API 由 esp32 Arduino core 提供。

## 如何判断结果

| 输出 | 含义与观察方法 |
| --- | --- |
| `FOUND 0x23 / 0x44 / 0x62` | 对应三个 I²C 地址有应答；还需要下方的有效读数，不能只凭地址就判定测量正常 |
| `SHT31 [OK]` | 本次温湿度读取有效，湿度没有减半；留意是否符合当前环境 |
| `BH1750 [OK]` | 本次光照读取有效；用手遮挡后应明显下降，移开后回升 |
| `SCD41 [WAIT]` | 等待首次测量，标准周期测量约 5 秒一份新值 |
| `SCD41 [NEW]` | 得到新的有效 CO₂ 读数 |
| `SCD41 [HOLD]` | 暂时显示上一份有效值，同时标注年龄；不是本轮新测量 |
| `SCD41 [STALE]` | 超过 15 秒无新测量，旧值作废，等待重新初始化 |
| `FAIL` | 初始化、总线通信或数据有效性检查失败，不用 0 或 400 ppm 伪装成正常读数 |
| `MQ2 [ADC_ONLY]` | 成功执行 ADC 采样，但模拟输入没有设备身份应答，不能自动证明 MQ-2 已接好或已预热 |

MQ-2 输出 32 次采样的平均 ADC 值、最小/最大值、ADC 引脚电压，以及分压前 AO 的估计电压。ADC 采用 12 位与最高衰减设置，原始值范围 0～4095；电压使用 `analogReadMilliVolts()`，不是简单按 3.3V 满量程换算。[乐鑫 ADC API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/adc.html)

短时间先观察读数与波动，不能把“有数字”当成烟雾检测或气体浓度校准通过。长期为零、贴近满量程、与万用表明显不符时，优先排查供电、共地、AO 和分压接线。`ADC_ONLY` 即使数字看起来正常也会保留，不自动切换成“MQ-2 正常”。

MQ-2 新器件需要预热和稳定过程；厂家标准测试条件的预热时间不少于 48 小时，不能把几分钟串口读数当成已经完成稳定性验证。本程序不自动认定预热完成、不设置烟雾报警阈值，也不输出烟雾 ppm。MQ-2 会发热，应与 SHT31 保持一定距离，观察其对温湿度的影响。[MQ-2 厂家说明](https://www.winsen-sensor.com/product/mq-2.html?by=mq-sensor)

SCD41 测量顺序按 [Sensirion 官方接口示例](https://github.com/Sensirion/arduino-i2c-scd4x/blob/master/examples/exampleUsage/exampleUsage.ino) 实现。

## 本地编译

```powershell
arduino-cli compile --fqbn esp32:esp32:esp32s3:CDCOnBoot=cdc --build-path artifacts/sensor-check-build firmware/sensor_check
```

命令只编译，不上传。四个传感器的实际读数需要在烧录后查看；编译通过不能代替实物测量。

2026-10-15 本机编译：Arduino CLI 1.1.1、esp32 core 3.0.7，以上 FQBN 编译通过。程序占 340713 字节，全局变量占 19264 字节；当时未上传。随后用户提供成功上传日志及 SHT31 / BH1750 / SCD41 有效读数、MQ-2 ADC 值。MQ-2 烟雾响应与基线仍待验证，详见 [验证记录](../../docs/VALIDATION.md)。联网与声光整合版见 [固件说明](../README.md)。
