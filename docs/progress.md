# 进度与验收记录

## 0.1.0.dev1 · 2026-11-11

| 需求 | 状态 | 验收证据 |
| --- | --- | --- |
| 基础-01 Windows 桌面窗口 | 完成 | 实际启动命令创建了 Windows 窗口，并在关闭窗口后正常退出。Qt 点击测试覆盖四个页面及对应详情 |
| 基础-02 本地存储 | 完成 | 首次启动创建 SQLite 数据库与受管工作区。测试覆盖页面偏好持久化和较新数据库版本保护 |
| 基础-03 工程可复现 | 完成 | 从锁文件安装依赖，完成代码检查、自动化测试及打包。需求、架构、开发流程和变更日志已归档 |
| P0-01 至 P0-05 | 待实现 | 文件接入、索引、检索、问答与评测 |
| P1-01 至 P1-03 | 待实现 | 标签、完全重复检测、整理与桌面端到端验收 |

本地验证环境为 Windows x64、CPython 3.12.13、PySide6 Essentials 6.12.0、SQLite 3.53.1 和 uv 0.11.21。

| 检查 | 实际结果 |
| --- | --- |
| `uv sync --locked` 与 `uv lock --check` | 通过，按锁文件安装 |
| `uv run --locked filemind` | Windows 窗口与测试数据库创建成功，关闭后退出码为 0 |
| `uv run --locked python -m filemind --check --data-dir .\data\check` | 结构版本 1，FTS5 探测为可用 |
| `uv run --locked ruff check .` 与 `uv run --locked ruff format --check .` | 通过 |
| `uv run --locked pytest` | 5 项通过，覆盖诊断、数据存储、导航与数据目录按钮 |
| `QT_QPA_PLATFORM=windows` 下运行 `uv run --locked pytest -q -p no:faulthandler tests/test_window.py` | 两项窗口测试通过。启用 pytest 异常处理器的首次运行输出 `0x8001010D` COM 诊断，测试进程正常结束 |
| `uv build --no-sources` | 生成源码包和 wheel |
| 文档链接 | 检查 21 个仓库内相对链接，目标文件均存在 |

GitHub Actions 工作流已配置，远端执行待验证。下一阶段从受管文件导入与索引任务开始。
