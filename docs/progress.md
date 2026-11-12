# 进度与验收记录

## 0.1.0.dev2 · 2026-11-12

| 需求 | 状态 | 验收证据 |
| --- | --- | --- |
| P0-01 导入与关联 | 完成 | 25 份样例全部建立索引。同名副本、目录只读、敏感路径排除、关联撤销和索引失败原因通过验证 |
| P0-02 解析、来源与更新 | 完成 | 验证物理页、幻灯片、段落、表格位置和行号，BGE WordPiece 分块，无文本 PDF 的 OCR 状态，修改后引用失效，以及完整扫描后清理缺失索引 |
| P0-03 智能检索 | 完成 | 实测关键词、真实本地 BGE 和混合检索。技术术语、文件聚合、范围过滤和缺模型降级通过验证 |
| P0-04 问答与引用 | 完成 | 本机默认摘录流程、限定证据、有效引用、错误 ID、版本与摘录、服务断开和问答期间原文变更均已验证。远程接口使用本地模拟服务 |
| P0-05 检索评测 | 完成 | 25 份样例、37 条查询。混合检索 Hit@5 为 31/32，MRR 为 0.935，预热后 p95 为 22.8 ms。无答案结果与复现命令见 [评测记录](evaluation.md) |
| P1-01 至 P1-03 | 待实现 | 标签、完全重复检测、整理及其端到端验收 |

本机为 Windows 11 x64、CPython 3.12.13、PySide6 Essentials 6.12.0、SQLite 3.53.1 和 uv 0.11.21。

| 检查 | 实际结果 |
| --- | --- |
| `uv sync --locked`、`uv lock --check`、Ruff 检查和格式检查 | 通过 |
| `uv run --locked pytest` | 18 项通过，包含以 `FILEMIND_TEST_MODEL` 指定真实本地模型的检索用例 |
| `QT_QPA_PLATFORM=windows` 下运行 `uv run --locked pytest -q -p no:faulthandler tests/test_window.py` | 3 项通过，覆盖窗口导航、数据目录入口、导入、检索、问答摘录和原文预览 |
| 桌面启动与退出 | 隔离目录启动成功，关闭后退出码为 0 并释放目录锁 |
| `uv build --no-sources` | 生成源码包和 wheel，wheel 中的词表通过 SHA-256 核对 |

第三方问答服务与大规模资料库性能尚待实测。向量检索评测使用 CPU。

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

桌面基础分支经 [PR #1](https://github.com/We1chan/FileMind/pull/1) 合入 `main`，合并前后的 Windows CI 均通过。
