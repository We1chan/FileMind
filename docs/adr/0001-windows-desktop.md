# ADR 0001 Windows 桌面方案

状态为已接受，日期为 2026-11-11。

FileMind 的早期规划以本地 Web 应用为入口。开发启动时将交付目标调整为 Windows 桌面应用，继续保留原规划的文件接入、检索、引用与受管文件整理能力。

使用 PySide6 的 Qt Widgets 构建 Windows 窗口。应用服务与界面运行在同一 Python 进程，耗时任务由后台工作线程处理。Python 保留后续文档解析与向量模型的集成路径。当前依赖只安装 Qt Widgets 所需的 PySide6 Essentials。采用标准库 `sqlite3` 维护 SQLite 数据与 FTS5，数据访问封装在独立模块。

框架阶段以 Windows x64 和 Python 3.12 验证。首版文件选择、预览和端到端测试由桌面界面提供。Windows 安装包在核心流程实现后评估。

- [Qt for Python 入门](https://doc.qt.io/qtforpython-6/gettingstarted.html)
- [PySide6 Essentials 发布信息](https://pypi.org/project/PySide6-Essentials/)
