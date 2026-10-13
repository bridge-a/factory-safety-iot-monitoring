# 接入与配置说明

本文包含 v0.1.0 基线及 main 分支的接入准备。实际 MQTT 代理、硬件和邮件收件链路仍需联调。新固件与树莓派准备步骤见 [第一阶段接入说明](HARDWARE_INTEGRATION.md)。

## 后台配置

环境变量在启动 `dashboard.py` 前设置，不会自动读取 `.env` 文件。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `FACTORY_MODE` | `hybrid` | `demo` / `hybrid` / `live` |
| `FACTORY_WEB_HOST` | `127.0.0.1` | HTTP 监听地址 |
| `FACTORY_WEB_PORT` | `5001` | HTTP 端口 |
| `FACTORY_MQTT_HOST` | `localhost` | MQTT 代理地址 |
| `FACTORY_MQTT_PORT` | `1883` | MQTT 端口 |
| `FACTORY_MQTT_USERNAME` | 空 | 可选的 MQTT 用户名 |
| `FACTORY_MQTT_PASSWORD` | 空 | 可选的 MQTT 密码，仅在本机设置 |
| `FACTORY_ENV_TOPIC` | `factory/workshop1/env` | 接收采样 |
| `FACTORY_ALERT_TOPIC` | `factory/workshop1/alert` | 告警发布主题 |
| `FACTORY_DATA_DIR` | 项目下的 `data/` | 数据库目录 |
| `FACTORY_MAIL_CONFIG` | 项目下的 `mail.ini` | 本机邮件配置路径 |

后台每 2 秒采样，ESP32 超过 20 秒没有新数据则视为过期。只运行一个后台进程；网页关闭不影响后台线程，后台退出则停止监测。当前 MQTT 连接面向可信局域网，支持可选用户名和密码，尚未提供 TLS 配置；凭据不返回到网页状态接口。

## MQTT 报文

向 `factory/workshop1/env` 发布 JSON：

```json
{
  "device": "esp32-s3-workshop1",
  "temp_c": 26.2,
  "rh": 52.1,
  "lux": 280,
  "co2_ppm": 650,
  "co2_valid": true,
  "smoke_raw": 350,
  "smoke_valid": true
}
```

- `smoke_raw` 为拟定的 12 位 ADC 相对读数，范围 0～4095，单位不是 ppm。
- `smoke_valid` 与 `co2_valid` 必须明确为 `true` 或整数 `1` 才使用对应数值。MQ-2 预热期间发送 `smoke_valid=false`。
- 温湿度可通过 `temp_c_valid=false`、`rh_valid=false` 或对应值为 `null` 表示无效。
- 接收时间由后台生成；ESP32 的 `millis()` 不能当作 Unix 时间。
- 后台忽略 retained 报文，避免旧值被当成刚采集的数据。
- `live` 缺失烟雾字段时显示待接入；`hybrid` 使用明确标注的模拟烟雾。
- 旧文物固件的 `museum/...` 主题不会被此项目接收，需要独立准备新固件。

本阶段新固件位于 `firmware/factory_sensors/`。MQ-2 尚未接入，固件实际发送 `smoke_raw=null, smoke_valid=false`，由混合模式补入模拟烟雾；上面的完整报文只是未来接入字段示例。固件与原草图独立，尚未烧录。

当前只管理一个区域、一个采集节点。多个节点需要增加设备白名单和独立区域状态。

## 演示阈值与告警规则

| 指标 | 黄色预警 | 红色报警 |
| --- | --- | --- |
| 温度 | ≥35 °C | ≥45 °C |
| 相对湿度 | ≥75 %RH | ≥85 %RH |
| CO₂ | ≥1000 ppm | ≥1500 ppm |
| 烟雾相对值 | ≥1200 ADC | ≥2000 ADC |

阈值集中在 `alert_rule.py`，用于课程演示，并非工业安全标准。MQ-2 到货后需根据预热基线、布置和实测结果调整。CO₂ 仅用于气体异常演示；光照不参与本版安全告警。

每个指标独立记录事件。首次异常立即记录，持续异常去重，等级升级立即通知；降级和恢复需连续 15 秒有效数据。缺失数据不能使已有报警恢复。不同运行模式的状态分别保存，避免切换模式产生虚假的恢复记录。

页面实时风险基于最新采样，历史事件反映曾经发生的告警。等级标签数量随搜索、模式、类别与时间范围变化，并独立于当前选中的等级。

## QQ 邮件

1. 将 `mail.ini.example` 复制为项目根目录的 `mail.ini`。
2. 在本机填入 QQ 发件地址、收件地址和 SMTP 授权码，将 `enabled` 设为 `true`。授权码不是 QQ 登录密码，不提交到仓库。
3. 执行 `python check_mail.py` 检查本地配置，再重启后台。该命令只检查配置，不连接邮箱或发送邮件。
4. 如需联调模拟事件邮件，再将 `allow_demo` 设为 `true`；此类邮件标题带“模拟演示”。默认不发送模拟邮件。

默认使用 `smtp.qq.com:465` 和 SMTP SSL。重试队列保存在告警数据库中；默认最多尝试 5 次，重试间隔从 30 秒增加至最多 10 分钟。事件和队列跨进程重启保留。

“已提交”表示 SMTP 服务器接受，不保证收件箱或手机推送必达。应实际验证关闭网页后的手机邮件接收。进程在投递期间异常退出可能造成重复投递，可用事件编号和 Message-ID 辨认；开启邮件不会补发此前标为“未启用”的历史事件。

## 存储与查询

- `data/environment.sqlite3`：按模式记录采样。
- `data/alerts.sqlite3`：告警状态、事件与邮件重试队列。
- 历史查询单次最多 24 小时，可选择任意已保存日期中的时间段。
- 曲线最多约 600 个抽样点；统计和 CSV 导出使用查询范围内的全部样本。
- 无效值导出为空白，模拟来源保留在事件快照和导出文件中。
- 查询模式可以与后台正在运行的模式不同。

数据库和真实邮件配置属于本机运行数据，已由 `.gitignore` 排除。
