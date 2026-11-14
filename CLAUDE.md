# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

产品范围、实现边界和验收要求以 [AGENTS.md](AGENTS.md) 为准，本文件只补充开发命令和跨文件的架构要点。仓库文档、界面文案和错误消息都使用中文。

## 常用命令

开发环境使用 Windows x64、PowerShell、Python 3.12 和 uv。每个 PowerShell 会话先加载开发环境脚本，把缓存、解释器和临时文件留在仓库的 `data/`。

```powershell
. .\scripts\dev-env.ps1
uv sync --locked
uv run --locked filemind                                   # 源码运行默认使用 .\data\library
uv run --locked filemind --data-dir .\data\demo            # 隔离数据目录；/data/ 已被 Git 忽略，也不会被应用索引
uv run --locked python -m filemind --check --data-dir .\data\check   # 无窗口诊断：版本、结构版本、FTS5
uv run --locked python -m filemind --install-model --data-dir .\data\p0   # 下载并校验 BGE ONNX 权重
```

CI（`.github/workflows/windows.yml`）按以下顺序检查，提交前在本地跑全：

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest
uv build --no-sources
```

- 单个测试：`uv run --locked pytest tests/test_library.py::test_binary_locations_and_ocr`，或用 `-k <关键字>`。
- `tests/test_window.py` 默认用 Qt 离屏平台。要用原生窗口复测，先设 `$env:QT_QPA_PLATFORM = 'windows'`（进度记录里配合 `-p no:faulthandler`）。`test_tray_activation_shows_the_native_window_after_hidden_launch` 总是启动原生窗口子进程。
- 真实模型测试需设 `$env:FILEMIND_TEST_MODEL` 为已安装的 ONNX 路径，否则跳过。`$env:FILEMIND_EMBED_DEVICE = 'cpu'` 强制 CPU 推理。
- 检索评测：`uv run --locked python -m samples.evaluate --model .\data\p0\models\bge-small-zh-v1.5.onnx --output .\data\p0\evaluation.json`。结果写入 `docs/evaluation.md`。
- 实盘评测用 `samples.evaluate_library` 只读打开现有资料库，标注与逐条报告留在 `data/evaluation/`。
- 远程问答读取 `FILEMIND_LLM_URL`、`FILEMIND_LLM_MODEL`、`FILEMIND_LLM_API_KEY`，还须在界面「设置与任务」中勾选启用。
- 依赖变更后运行 `uv lock`，同时提交 `pyproject.toml` 和 `uv.lock`，用 `uv lock --check` 核对。
- Ruff 行宽 100，规则集 `E4/E7/E9/F/I`。

## 架构要点

### 进程与线程

- `__main__.main` 解析命令行。每个数据目录只允许一个进程：用 `QLockFile`（`filemind.lock`）加锁，再用 `QLocalServer` 监听以数据目录路径哈希命名的通道。第二个实例发送 `show` 唤起已有窗口后退出。`--tray` 启动到托盘；关闭窗口只是隐藏到托盘，退出走托盘菜单。
- `ui.window.MainWindow` 创建并持有 `Library`、`Search`、`Questions` 和 `ContinuousIndex`。`Search` 和 `ContinuousIndex` 共用同一个 `SemanticIndex`（同一个 ONNX 推理会话）。
- 交互操作通过 `MainWindow._start` 放到 `Task(QThread)` 里执行，同一时间只运行一个。操作函数接收一个 `cancelled` / `stop` 回调，耗时循环要轮询它。写操作传 `writes=True`，会进入 `ContinuousIndex.foreground()` 并持有写锁，后台索引让路。`FileAccessError` 的消息直接显示在状态栏，其他异常记日志后显示通用提示。
- `ContinuousIndex` 有两个守护线程：索引线程处理 watchdog 事件（约 2 秒合并一批）、每 10 分钟补扫、每批 256 个文件登记；向量线程独立补建 embedding。

### 数据与存储

- `config.AppPaths` 定义数据目录结构：`filemind.sqlite3`、`workspace/`（受管导入副本）、`models/bge-small-zh-v1.5.onnx`。从源码运行默认使用仓库的 `data/library/`，安装版默认使用 `%LOCALAPPDATA%\FileMind`。两者都支持 `--data-dir`。`Library` 排除应用数据、Windows 系统保护目录、内置噪声目录和用户设置的排除目录。
- `storage.LocalStore` 每次操作打开一个短连接，使用 WAL。迁移集中在 `initialize()`，按 `PRAGMA user_version` 递进（当前 `SCHEMA_VERSION = 4`）。遇到更高版本时抛出 `UnsupportedSchemaError`，且不修改数据。改表结构时要提升版本号、补迁移步骤并在 `tests/test_storage.py` 加用例。
- 表关系：`sources`（关联目录）→ `files`（`source_id IS NULL` 表示受管导入，否则是只读关联目录下的 `relative_path`）→ `sections`（未分词的原文及位置）→ `chunks`（段内偏移、位置、jieba 词项、`model_fingerprint` 和 `vector`）。`chunks.id` 是 AUTOINCREMENT，内容更新后旧块的向量写入会被自然跳过。
- `chunk_fts` 和 `file_catalog` 两张 FTS5 表是派生数据，启动时若缺失或存在 `fts5_dirty` 偏好会重建。`chunk_fts` 从 `chunks` 中已存的词项重建，所以修改 `text_index.keyword_terms` 只影响重新索引的文件。

### 索引流水线（`Library._index`）

1. `path_for` 和 stat 检查：拒绝链接、隐藏文件、排除目录、越出受管工作区或关联根目录的路径。
2. 用 `(size, mtime_ns, ctime_ns, dev:ino)` 指纹和解析版本判断是否需要读取文件。SHA-256 和解析版本都未变化时只更新 stat。
3. `store.begin_index` 增加内容版本，文件暂时退出检索。
4. 解析：`.md/.txt` 在进程内处理；PDF 和 Office 在子进程 `python -m filemind.documents <ext>` 中处理（stdin 输入字节，stdout 输出 JSON，超时 25 秒）。压缩包、EXE 和超过 32 MiB 的文件走 `file_metadata`，状态为 `cataloged`。调试解析问题时可以直接运行这个子进程命令查看 JSON。
5. Word 合并相邻短段及短表格单元，Excel 合并相邻工作表行。调整 Office 分块后提升 `OFFICE_PARSER_VERSION`（当前为 1），旧版本正文会重新索引。`text_index.split_section` 用仓库词表按 WordPiece 切块（384 token，重叠 48）。
6. 再次 stat，确认索引期间文件没有变化，然后 `store.finish_index` 在一个事务中写入 sections、chunks 和 FTS。

文件状态取值：`pending`、`ready`、`cataloged`、`ocr`、`empty`、`failed`、`missing`。来源扫描完整后才把缺失文件标为 `missing` 并清理索引。

### 检索与问答

- `Search.find` 的 `filename` 模式按名称与路径返回各文件。关键词用 FTS5 BM25 优先查找全词项命中。三个及以上词项的查询可放宽，在 BM25 候选中保留同一块覆盖至少一半词项的结果。合并 `file_catalog` 的名称和路径命中后，关键词与语义按非空文件的 SHA-256 折叠，`hybrid` 再做 RRF（k=60）。返回前用 `library.metadata_current` 或 `catalog_current` 复核候选文件。缺少模型时降级为关键词检索并提示「语义检索未启用」。
- `SemanticIndex` 按余弦相似度排序并过滤低于 0.53 的候选，按检索范围缓存向量矩阵。新增向量可增量载入，索引无向量的文件继续复用缓存，删除带向量的块会触发重新读取。
- `embeddings.BgeModel` 加载前校验权重大小和 SHA-256。Windows x64 优先使用 DirectML（依赖 `onnxruntime-directml`），失败时回退 CPU。
- `qa.Questions.ask` 先检索并核对范围内的证据。远程问答需显式启用，最多发送 6 条摘录，每条不超过 900 字符。请求禁止重定向，网络操作超时为 60 秒。Chat Completions 请求使用 JSON 模式，明确拒绝该参数的接口可使用纯文本 JSON。返回的编号、块 ID 和版本须一致，摘录展示本地原文。证据不足、服务失败和原文变更分别提示。

### 界面

`ui/window.py` 负责窗口、托盘、任务调度和动作连线，`ui/pages.py` 负责页面控件，`ui/theme.py` 负责样式。四个导航页是文件库、智能检索、智能整理和设置与任务，上次打开的页面存在 `preferences.last_page`。

## 测试约定

- `tests/conftest.py` 的 `library` fixture 在 `tmp_path` 下创建 `AppPaths`、`LocalStore` 和 `Library`。测试用 dev 依赖 `python-docx` / `python-pptx` 现场生成样例文件。
- `pytest` 配置了 `pythonpath = ["."]`，所以可以用 `python -m samples.*` 运行 `samples/` 下的评测脚本。

## 文档与版本

- 版本号以 `pyproject.toml` 的 `project.version` 为准，开发版格式为 `0.1.0.devN`。分支命名用 `feat/<主题>` / `fix/<主题>`。
- 变更涉及需求、架构或用法时，按 `docs/README.md` 的维护表在同一变更中更新文档。`docs/progress.md` 要写明实际环境、命令、结果和未验证项，`CHANGELOG.md` 记录用户能感知的变化。
