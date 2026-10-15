# 第三方组件说明

## Apache ECharts 5.6.0

- 用途：历史趋势图；浏览器从本地静态文件加载，不依赖 CDN。
- 文件：`static/vendor/echarts.min.js`。
- 项目：https://github.com/apache/echarts/tree/5.6.0
- 许可证：[Apache ECharts LICENSE](static/vendor/ECHARTS-LICENSE.txt)。
- 声明：[Apache ECharts NOTICE](static/vendor/ECHARTS-NOTICE.txt)。

保留发行文件中的第三方版权及许可注释。ECharts 自带依赖声明以随附 LICENSE 和 NOTICE 为准。

## Python 依赖

Flask、paho-mqtt，以及可选开发依赖 pytest、Playwright 由包管理器安装；本仓库不包含其源码分发或虚拟环境，各依赖遵循其自身许可证。

可选视觉依赖 NumPy、Pillow、[ONNX Runtime](https://onnxruntime.ai/docs/install/) 由 `requirements-vision.txt` 安装，本项目未复制其源代码。

## 可选预训练火焰权重

- 来源：[fiacecson20/cctv-ai-fire-smoke](https://huggingface.co/fiacecson20/cctv-ai-fire-smoke)，固定提交及 SHA-256 见 `models/fire-model.json`。
- 模型卡将该权重标为 MIT，同时提醒 Ultralytics 基础模型涉及 AGPL；实际下载的 ONNX 元数据直接标注 [AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html)。保留来源与两种标记，不将该冲突解释为统一的 MIT 授权。公开 Git 仓库不分发权重，启用脚本从原发布者下载。
- 本地部署包可含同一份未修改、经校验的 ONNX 文件；模型元数据完整保留。本项目自编写预处理、输出解码和后处理，不复制 Ultralytics 实现，也不宣称自行训练该权重。
- 训练来源按上游模型卡描述；其链接的完整训练报告和数据来源未能全部核实，实景效果仍需验证。详见 `models/README.md`。

## 固件依赖

ESP32 Arduino core、Adafruit SHT31 / BusIO、BH1750、PubSubClient、Sensirion Core / I2C SCD4x 通过 Arduino 工具安装，不随仓库分发。版本与官方接口参考见 [固件说明](firmware/README.md)。新固件调用这些库的公开接口，没有复制其库源码或整份示例。

## 设计参考

辅助页面参考了 [shadcn-admin](https://github.com/satnaing/shadcn-admin)、[Tabler](https://github.com/tabler/tabler) 的列表组织与布局思路，以及 [Ant Design 数据展示规范](https://ant.design/docs/spec/data-display/)。未复制整套模板或第三方示例业务数据。

历史趋势页另参考 [Grafana 时间范围控件](https://grafana.com/docs/grafana/latest/visualizations/dashboards/use-dashboards/) 与 [Ant Design 可视化页面规范](https://ant.design/docs/spec/visualization-page/)，用于确定查询操作、图表与明细的层次；实现继续使用现有 Flask、原生前端与 ECharts。

明火检测页参考 [Frigate Live View](https://docs.frigate.video/usage/live/) 的摄像头优先布局、离线画面和近期事件组织，并查看了其公开演示。未复制 Frigate 的源码、摄像头画面或其他素材，页面仍由本项目原生前端实现。

## 可选摄像头系统依赖

USB 摄像头通过系统已安装的 [FFmpeg](https://ffmpeg.org/) 命令行程序采集 V4L2 / MJPEG。本仓库不包含或分发 FFmpeg 二进制；安装版本的许可证取决于其构建选项，详见 [FFmpeg Legal](https://ffmpeg.org/legal.html)。Python 采集代码和网页预览代码由本项目实现。

设备管理页另参考 [shadcn-admin 用户列表](https://shadcn-admin.netlify.app/users) 的紧凑列表和弱化的辅助信息，以及 [ThingsBoard 设备文档](https://thingsboard.io/docs/user-guide/devices/) 对设备连接状态、最新遥测与详情的组织方式。没有引入这些平台、复制代码或使用其示例业务数据。

本项目暂未指定项目级开源许可证；第三方组件的许可证不自动适用于本项目的其他代码。

模拟与联调页参考 [shadcn-admin 设置页](https://shadcn-admin.netlify.app/settings) 的表单层次和分隔方式，以及 [IBM Carbon Structured list](https://www.carbondesignsystem.com/building-blocks/core/components/structured-list/guidelines) 的行式选项与状态区分。仅借鉴信息组织，未复制源码、截图或素材；交互使用本项目的原生按钮与既有接口。
