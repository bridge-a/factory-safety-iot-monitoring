# 声光同步测试

独立 ESP32-S3 草图：GPIO5 与 GPIO6 同时置高约 2 秒，同时置低约 2 秒，持续循环。用于观察 LED 与有源蜂鸣器是否同步响应，不读取传感器，不联网，也不判断告警阈值。

## 接线与范围

- LED：GPIO5 经 330Ω 限流电阻接正极，负极接 GND；470Ω 或 1kΩ 也可用于此低电流指示测试。
- GPIO6：高电平使有源蜂鸣器开启，低电平关闭。使用驱动模块时应连接其高电平有效的控制输入，并共地。
- 用户当前尝试裸蜂鸣器直接驱动时，接法为正极接 GPIO6、负极接 GND；不要把蜂鸣器正极接 5V、负极接 GPIO6。测试程序不验证负载电流，能够发声不代表 GPIO 长期带载条件已验收；负载电流或瞬态不满足 GPIO 规格时应使用驱动电路。出现开发板异常发热、复位时断电检查。
- GPIO 编号不是排针的物理顺序。此草图限定 ESP32-S3，不能直接套用到 GPIO6 被 Flash 占用的经典 ESP32。

## 使用

1. 在 Arduino IDE 打开本目录的 `alarm_check.ino`，沿用此前成功上传的 ESP32-S3 板型、端口和 USB 设置，不需要安装额外库。
2. 上传后应看到 LED 亮、蜂鸣器响约 2 秒，然后同时停止约 2 秒，并不断重复。
3. 可打开 115200 波特率串口监视器，观察交替输出的 `ON` / `OFF`。程序不等待串口连接。
4. 上传会替换板上当前运行的程序；原有采集源码保留，重新上传 `sensor_check.ino` 即可回到传感器测试。

本草图只提供同步输出的功能测试，实物响应需要烧录后观察；不作为传感器越限、MQTT 命令或平台声光联动已经完成的依据。

## 编译

```powershell
arduino-cli compile --fqbn esp32:esp32:esp32s3:CDCOnBoot=cdc --build-path artifacts/alarm-check-build firmware/alarm_check
```

2026-10-15 本机使用 Arduino CLI 1.1.1、esp32 core 3.0.7 编译通过：程序占 297841 字节，全局变量占 18520 字节。编译检查时未上传；随后用户确认调整接线后 LED 和蜂鸣器均可同步开关。该结果不包含整合告警联动与负载电流验收。

接口依据：[Arduino-ESP32 GPIO API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/gpio.html)。
