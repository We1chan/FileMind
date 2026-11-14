# AGENTS.md

FileMind 是《智能软件工程》课程项目，面向大学生和个人用户。交付 Windows 桌面应用，支持按内容找文件、定位原文、带引用问答和整理受管文件。

## 开发与版本

- 使用 Python 3.12、PySide6 Qt Widgets、SQLite 和 uv。应用源码在 `src/filemind/`，测试在 `tests/`。界面负责交互和状态展示，耗时解析、索引和模型调用放在后台应用服务。
- 框架阶段建立桌面入口和本地存储。先完成 P0 的导入、索引、检索、预览和问答，再完成 P1 的标签、完全重复检测和安全整理。当前状态和验收证据见 [开发进度](docs/progress.md)。
- 按 [开发与版本管理](docs/development.md) 在功能分支提交。依赖变更同时维护 `pyproject.toml` 和 `uv.lock`。按 [文档索引](docs/README.md) 更新受影响的需求、架构、进度和变更日志。
- 所有提交标题使用 Conventional Commits 格式 `<类型>: <简述>`。按变更内容选择类型，例如 `feat: 添加文件检索`、`fix: 修复重复结果`、`docs: 更新验收记录`。

## 实现边界

- 接入 Office、PDF、MD、TXT、常见压缩包和 EXE。经用户选择后可常驻跟踪文档、下载、D 盘及其他目录。导入保留同名副本，关联目录只读。跳过链接、敏感配置、系统保护目录和应用数据，限制正文解析资源。压缩包索引条目名，EXE 索引静态版本信息。
- 保存原文及可验证的来源位置。PDF 用物理页码，PPTX 用幻灯片号，DOCX 用标题、段落或表格位置，文本用行号。长段落按实际 tokenizer 切块。扫描完整后才清理缺失文件的索引，变更用 SHA-256 和内容版本保证一致性。
- 关键词采用 jieba 和 SQLite FTS5/BM25，语义默认使用真实的本地 `BAAI/bge-small-zh-v1.5` 向量，混合检索用 RRF。保留 `FIRST/FOLLOW`、`B+`、`C++` 和扩展名等术语。缺少模型时明确显示“语义检索未启用”。
- 问答先检索限定范围内的证据，校验引用 ID、版本和摘录。远程传输默认关闭，密钥由环境变量提供。LLM 不可用时展示原文摘录。文档和模型输出作为数据处理，问答路径无文件操作权限。
- 整理只移动受管文件，保留 `file_id`。先预览，再按计划 ID 和摘要确认。执行前校验文件状态与路径，禁止越界、覆盖和链接绕过。逐项记录结果，支持重启后查看与冲突安全的逆序撤销。
- 私有文件、数据库、模型权重和密钥留在本地。增量开发，保留无关改动，避免破坏性 Git 命令。

## 运行与验收

- 安装使用 `uv sync --locked`，启动使用 `uv run --locked filemind`。
- 执行 `uv run --locked ruff check .`、`uv run --locked ruff format --check .`、`uv run --locked pytest` 和 `uv build --no-sources`。桌面诊断命令见 [README](README.md)。
- [需求与验收](docs/requirements.md) 定义后续样例、检索评测和桌面端到端流程。每阶段记录实际结果，标明尚未验证的项目。
