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

## 设计参考

辅助页面参考了 [shadcn-admin](https://github.com/satnaing/shadcn-admin)、[Tabler](https://github.com/tabler/tabler) 的列表组织与布局思路，以及 [Ant Design 数据展示规范](https://ant.design/docs/spec/data-display/)。未复制整套模板或第三方示例业务数据。

本项目暂未指定项目级开源许可证；第三方组件的许可证不自动适用于本项目的其他代码。
