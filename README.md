# FileMind

FileMind 是《智能软件工程》课程项目。Windows 桌面开发版支持接入 PDF、DOCX、PPTX、MD 和 TXT，按内容查找文件，定位原文并用资料回答问题。文件与索引保存在本机。

## 在 Windows 上运行

在仓库根目录打开 PowerShell。开发环境使用 Windows x64、Python 3.12 和 uv。尚未安装 uv 时，可运行 `winget install --id astral-sh.uv --exact`，再重新打开 PowerShell。

```powershell
uv sync --locked
uv run --locked filemind
```

首次启动会在 `%LOCALAPPDATA%\FileMind` 下初始化 SQLite 数据库和受管工作区。设置页可以打开数据目录。应用会记住上次打开的页面。

在「文件库」选择「导入文件」可保存受管副本。同名文件各自保留。「关联目录」会只读扫描所选目录，可在「设置与任务」取消关联。启动和点击「刷新索引」时会检查文件变动。选中文件可查看失败原因并重试。无可提取文本的 PDF 标为「需要 OCR」。

在「智能检索」输入关键词或问题，按来源、格式或所选文件限定范围。点击结果或问答引用可预览对应的页、幻灯片、段落、表格位置或文本行。文本支持 UTF-8、带 BOM 的 UTF-16 和 GB18030。

### 语义检索

在本机安装 BGE Small 中文模型后，可使用语义与混合检索。以下命令下载约 95 MB 的 ONNX 权重并校验 SHA-256。缺少权重时应用显示「语义检索未启用」并使用关键词检索。

```powershell
uv run --locked python -m filemind --install-model
```

### 带引用问答

问答默认展示带位置的原文摘录。使用兼容 Chat Completions 的服务时，在启动前设置 `FILEMIND_LLM_URL`（完整的 HTTPS 接口地址）、`FILEMIND_LLM_MODEL` 和 `FILEMIND_LLM_API_KEY`。每次启动后，可在「设置与任务」勾选「允许远程问答」。问答只发送当前问题和限定范围内的相关摘录及引用编号。服务不可用或引用校验失败时，应用展示仍可核对的摘录。

### 独立数据目录与诊断

如果需要隔离演示或测试数据，可以显式指定目录。

```powershell
uv run --locked python -m filemind --install-model --data-dir .\data\demo
uv run --locked filemind --data-dir .\data\demo
```

无需打开窗口即可检查版本、SQLite 结构版本和 FTS5 支持。诊断会初始化指定目录的数据库。

```powershell
uv run --locked python -m filemind --check --data-dir .\data\check
```

## 开发文档

- [文档索引](docs/README.md)
- [开发与版本管理](docs/development.md)
- [开发进度与验收记录](docs/progress.md)
- [检索评测](docs/evaluation.md)
- [变更日志](CHANGELOG.md)

P0 和 P1 的范围与验收条件见 [需求与验收](docs/requirements.md)。
