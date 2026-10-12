# 首版验证记录

版本：v0.1.0。验证范围为软件和隔离模拟流程，实机与真实邮件接收尚未验收。

## 环境与方法

- Windows、Python 3.12.7、Flask 3.0.3、paho-mqtt 2.1.0。
- pytest 7.4.4；Playwright 1.59.0 / Chromium。
- 从 Git 暂存文件导出独立目录后运行验证，确认不依赖未上传的本机文件。
- 使用现有已安装依赖的虚拟环境运行，没有重新安装一套依赖；发布文件与本地运行数据分离。
- 每次测试使用独立数据库；邮件测试使用替身发送函数，不向外部邮箱发送。

## 验证结果

验证日期：2026-10-12。

| 检查 | 结果 | 覆盖范围 |
| --- | --- | --- |
| 后端 pytest | 14 / 14 通过 | 数据有效性、模式隔离、告警去重、升级与恢复、状态重启、邮件替身重试、API、历史与 CSV、筛选和计数 |
| 六页面浏览器回归 | 8 组检查通过 | 模拟告警、恢复等待、历史导出、关闭网页后后台持续采样、断线读数清空 |
| 告警页浏览器检查 | 12 组检查通过 | 空状态、筛选计数、分页、错误恢复、抽屉、触发指标高亮、实时风险与旧事件分离 |
| 页面布局 | 1920、1366、390 像素宽度通过 | 六页面未出现整页横向溢出，移动端表格可在容器中滚动 |
| 浏览器 JavaScript 异常 | 0 | 两组浏览器脚本均未记录页面异常 |
| 前端脚本语法 | 通过 | `node --check static/js/dashboard.js` |
| 发布内容 | 通过 | 本地 Markdown 链接、敏感信息模式扫描、配置与运行产物忽略规则 |

首次在导出目录运行测试时发现临时目录的父目录不存在；已补正文档中的创建步骤，按修正后的步骤运行 14 项后端测试全部通过。

## 复现方式

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
New-Item -ItemType Directory -Force .test-tmp | Out-Null
$qaTempPath = Join-Path '.test-tmp' ([guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -m pytest -q --basetemp=$qaTempPath
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe tests/browser_check.py
.\.venv\Scripts\python.exe tests/alerts_ui_check.py
```

Linux 使用 `.venv/bin/python`；后端测试可以执行 `.venv/bin/python -m pytest -q`。Linux 首次安装浏览器时，可使用 `.venv/bin/python -m playwright install --with-deps chromium` 安装系统依赖。

浏览器检查生成的截图和 JSON 报告分别位于 `artifacts/browser-check/`、`artifacts/alerts-check/`，均不提交。README 仅保留少量明确标为模拟数据的展示截图。

## 未验证范围

- 树莓派持续运行、真实 MQTT 代理与 ESP32 上报。
- MQ-2 预热、读数、阈值和传感器布置；蜂鸣器与 LED 实物动作。
- QQ SMTP 实际投递、收件箱和手机推送。
- USB 摄像头采集、明火模型的识别效果与推理性能。
- 工业环境适应性、现场验收与完整课程报告。

后续每完成一个实机环节，应补充条件、结果和证据，不能用模拟通过替代硬件验收。
