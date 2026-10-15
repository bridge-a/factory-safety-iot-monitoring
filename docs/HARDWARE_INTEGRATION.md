# 第一阶段：树莓派部署与现有传感器接入

本阶段目标：ESP32-S3 上报真实温度、湿度、CO₂、光照；树莓派以 `hybrid` 模式运行，烟雾保持模拟。网页、历史与告警共用同一后台，原文物项目独立保留。

**当前尚未完成烧录验证、树莓派部署或真实 MQTT 验收。** 2026-10-15 用户报告 MQ-2 已到货并接线，本机发现 ESP32 USB 串口，现提供 [四传感器串口测试](../firmware/sensor_check/README.md) 核对实测读数。树莓派 SSH 地址、用户名、运行环境与现有 MQTT 监听情况仍待确认；邮件实收、声光和视觉检测按后续阶段处理。

2026-10-13 本地验证结果：

| 项目 | 结果与范围 |
| --- | --- |
| ESP32-S3 固件 | Arduino CLI 1.1.1 / esp32 core 3.0.7，`esp32:esp32:esp32s3` 编译通过；使用空配置，未上传 |
| 固件体积 | 程序 900429 字节、全局变量 42416 字节；仅对应上述通用编译参数，不代表实机性能 |
| 后端 | 19 项 pytest 检查通过，含 5 项新增传感器报文与可选 MQTT 登录检查 |
| 部署脚本 | 三个 Bash 脚本逐一语法检查通过；Linux 安装、systemd 运行与代理连接待实测 |
| 原项目 | 31 个文物项目文件及旧 ESP32 草图 SHA-256 未变化 |

软件检查使用隔离数据库与网络替身，验证接口、历史和告警行为，不是物理传感器或真实代理测试。详细范围见 [验证记录](VALIDATION.md)。

## 1. 准备独立目录

树莓派开机，与电脑、ESP32 位于可互通的局域网。SSH 使用树莓派实际用户名；不要假定用户名一定为 `pi`。仅在树莓派的新目录执行：

```bash
cd ~
git clone https://github.com/bridge-a/factory-safety-iot-monitoring.git
cd factory-safety-iot-monitoring
```

若这个目录已经存在，先检查它的内容和 Git 状态，不覆盖。不要复制 Windows 的 `.venv`，不要把文物项目数据库放入本目录。

先检查系统与已有服务：

```bash
python3 --version
systemctl is-active mosquitto
ss -lntp
```

目标是保留原文物服务，确定 MQTT 当前使用的端口、监听地址和认证方式，并确认 `5001` 空闲。若缺少 Git、venv 或 Mosquitto 客户端，在核对系统后用系统包管理器补齐；已有 Mosquitto 不重装、不覆盖配置。

在 Debian / Raspberry Pi OS 上，缺少 Python venv 时可安装 `python3-venv`；需要命令行订阅时安装 `mosquitto-clients`。安装系统包需要树莓派本地的 sudo 权限。

## 2. 准备后台环境

```bash
bash deploy/raspberry-pi/prepare.sh
nano deploy/raspberry-pi/runtime.env
```

脚本只准备当前仓库的 Linux 虚拟环境、数据目录和本地环境文件，不修改 MQTT 服务，也不改动原项目。已有 `runtime.env` 保留。

首阶段配置：

| 配置 | 值 |
| --- | --- |
| `FACTORY_MODE` | `hybrid` |
| `FACTORY_WEB_HOST` | `0.0.0.0`，供局域网访问 |
| `FACTORY_WEB_PORT` | `5001`；如已占用，选其他空闲端口 |
| `FACTORY_MQTT_HOST` | 同机 MQTT 通常为 `127.0.0.1` |
| `FACTORY_MQTT_PORT` | 与已确认的代理一致，通常 `1883` |
| `FACTORY_ENV_TOPIC` | `factory/workshop1/env` |
| `FACTORY_ALERT_TOPIC` | `factory/workshop1/alert` |

需要 MQTT 登录时在本地文件填写 `FACTORY_MQTT_USERNAME`、`FACTORY_MQTT_PASSWORD`，在 ESP32 本地配置中填写相应凭据；空用户名保持匿名连接。MQTT 暂不支持 TLS。

`runtime.env` 由 Bash 加载，仅存放本机维护的 `KEY=value` 配置，字符串需按 Bash 规则加引号。不要把陌生脚本或命令粘贴进去。真实凭据只保存在本地，Git 已忽略该文件。Python 入口本身不自动读取 `.env`。

## 3. 确认 MQTT 可访问

优先复用树莓派已有代理，工厂项目使用独立主题。先确认它能同时接受后台与 ESP32 的连接。Mosquitto 2 默认启动行为可能只监听本机；“电脑上看到 1883”并不自动表示 ESP32 能连上，参见 [Mosquitto 官方迁移说明](https://mosquitto.org/documentation/migrating-to-2-0/)。

若已有代理不能复用，先确认 `1884` 空闲，可在可信局域网联调时另启工厂代理，避免修改原服务：

```bash
mosquitto -c deploy/raspberry-pi/mosquitto-factory.conf.example
```

这个可选示例在前台运行、允许匿名连接，不会替换系统 Mosquitto 服务；退出终端即停止。使用它时，后台和 ESP32 的 MQTT 端口都必须改为 `1884`。长期运行方式等确认实机后再配置。不要在已有代理可用时重复启动。

可使用客户端只读订阅：

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -t factory/workshop1/env -C 3 -W 30
```

按实际端口与认证方式调整。ESP32 尚未上传或连接时，订阅超时属于预期，不能填入假的“真实值”来代替验收。

## 4. 前台启动，确认后再设开机运行

```bash
bash deploy/raspberry-pi/run.sh
```

电脑浏览器访问 `http://树莓派IP:5001`。未收到 ESP32 上报时，温湿度、CO₂ 和光照为空，烟雾显示模拟值。此时不能以页面能打开作为硬件在线证据。

确认前台正常后先按 `Ctrl+C` 停止该实例，再执行：

```bash
bash deploy/raspberry-pi/install-service.sh
systemctl status factory-monitor.service
journalctl -u factory-monitor.service -n 60 --no-pager
```

脚本生成 `factory-monitor.service`，以当前普通用户运行，开机启动并在异常退出后重启。安装需 sudo；已有同名服务时脚本拒绝覆盖。服务先使用 `systemd-analyze verify` 检查配置，然后安装。

这个阶段沿用 Flask 自带服务器做局域网联调；systemd 负责进程生命周期，并不使它变成面向公网的生产服务器。服务必须只运行一个实例，避免重复采样与通知；不要再同时执行 `dashboard.py`、旧的采集入口或多个 WSGI worker。

日常操作：

```bash
sudo systemctl restart factory-monitor.service
sudo systemctl stop factory-monitor.service
sudo systemctl start factory-monitor.service
```

只停止或重启这个新服务；不停止原文物项目或共用的 MQTT 服务。服务重启后模式取 `runtime.env`，历史数据保留。

## 5. ESP32 烧录准备

见 [固件说明](../firmware/README.md)。保留原草图，从 `firmware/factory_sensors/` 打开新草图，填写被 Git 忽略的 `config.local.h`。MQTT 主机填写树莓派局域网地址，不能填 ESP32 自己的 `localhost`。

本阶段的报文示例（仅说明字段，不向真实主题注入）：

```json
{
  "device": "esp32-s3-workshop1",
  "uptime_ms": 6000,
  "temp_c": 26.25,
  "temp_c_valid": true,
  "rh": 61.4,
  "rh_valid": true,
  "lux": 280.0,
  "co2_ppm": 650,
  "co2_valid": true,
  "smoke_raw": null,
  "smoke_valid": false
}
```

固件没有湿度演示缩放；CO₂ 尚无有效结果时用 `null`，不使用 400 ppm 占位。断网时串口仍输出采样，恢复后只发送最新值。串口数据、订阅报文和网页读数应能对应。

## 6. 实机验收清单

| 检查 | 通过条件 | 当前结果 |
| --- | --- | --- |
| 进程与页面 | 新服务运行，六页可访问，原文物服务保持原状 | 待实测 |
| MQTT | 新主题连续上报，设备显示在线，接收时间持续更新 | 待实测 |
| 来源 | 温湿度、CO₂ 为实际数据，光照来自硬件，烟雾为模拟 | 待实测 |
| 数据一致性 | 串口、MQTT、网页数值对应，湿度未缩放 | 待实测 |
| 初始无效值 | SCD41 首次有效测量前显示待数据，不显示伪造基线 | 待实测 |
| 独立故障 | 某传感器失效不显示 0，其余传感器仍更新；故障不冒充恢复 | 待实测 |
| 通信断开 | 停止 ESP32 上报超过 20 秒，真实指标变缺失；不影响模拟烟雾标签 | 待实测 |
| 重连 | 恢复网络后重新在线，不回放积压报文；离线时串口仍采样 | 待实测 |
| 历史 | 选择混合模式能查询、导出当前真实指标与模拟烟雾，缺失值留空 | 待实测 |
| 后台独立 | 关闭浏览器仍入库；重启新服务后历史保留 | 待实测 |

故障注入应通过停上报、网络断开或在断电后调整接线进行，不带电随意插拔传感器。若测试共用代理断线会影响原项目，应改用独立测试代理。

首次验收保存：Git 提交号、树莓派系统与 Python 版本、开发板型号及上传参数、固件编译信息、串口/订阅记录、历史导出和页面截图。记录放本地 `artifacts/hardware-stage1/`，公开前去除凭据与私有网络信息。

完成这一阶段后再做 QQ 邮件实际接收；本次不会自动开启或发送邮件。
