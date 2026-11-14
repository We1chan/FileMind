# FileMind

FileMind 是《智能软件工程》课程项目。Windows 桌面开发版可持续索引本机文件，按名称、路径或内容查找资料，定位原文并用资料回答问题。文件与索引保存在本机。

## 在 Windows 上运行

在仓库根目录打开 PowerShell。开发环境使用 Windows x64、Python 3.12 和 uv。尚未安装 uv 时，可运行 `winget install --id astral-sh.uv --exact`，再重新打开 PowerShell。

```powershell
. .\scripts\dev-env.ps1
uv sync --locked
uv run --locked filemind
```

从源码启动时，SQLite 数据库、受管副本和模型保存在仓库的 `data/library/`。开发命令在 `data/` 下存放缓存、Python 和临时文件。设置页可以打开当前资料库目录。单独安装的应用可用 `--data-dir` 指定存放位置。

### 常驻索引

在「文件库」点击「接入常用位置」，可只读关联当前 Windows 用户的文档、下载，以及 `D:\`。也可单独选择其他目录。首轮先分批登记文件名和路径，再在后台读取正文与文件信息。按「关闭」会收进系统托盘，新增、修改和删除会继续更新。托盘菜单可打开、暂停、刷新或退出。默认资料库可在「设置与任务」启用「登录 Windows 时启动」。

「导入文件」可保存受管副本，同名文件各自保留。「设置与任务」可管理排除目录、取消关联和查看失败原因。扫描跳过常见缓存、构建产物和工具安装目录。排除目录里的旧索引会清理，恢复后重新纳入扫描。选择结果后点击「定位文件」，会在资源管理器中选中原文件。

### 文件检索

| 类型 | 索引内容 |
| --- | --- |
| Word、PowerPoint、Excel | DOCX/DOCM、PPTX/PPTM、XLSX/XLS/XLSM/XLSB 的可读正文或单元格值。DOC/PPT 由 Windows 已安装的文档筛选器提取正文 |
| PDF、MD、TXT | PDF 可提取文本、Markdown 和纯文本。扫描件在文件库标为「需要 OCR」 |
| ZIP、7Z、RAR、TAR 等 | 文件名、路径、包内条目名 |
| EXE | 文件名、路径、说明、产品、公司和版本等可用信息 |

全文解析上限为单个文件 32 MiB。更大的文件可按名称和路径检索。压缩包读取目录条目，程序文件读取静态版本资源。

在「智能检索」可选「文件名与路径」快速找文件，也可输入关键词或问题，按来源、格式或所选文件限定范围。「文件名与路径」逐个显示匹配的文件。内容检索合并 SHA-256 相同的非空文件，并显示匹配范围内的副本数。点击结果或问答引用可预览对应的页、幻灯片、段落或行范围。文本支持 UTF-8、带 BOM 的 UTF-16 和 GB18030。

### 语义检索

在本机安装 BGE Small 中文模型后，可使用语义与混合检索。以下命令下载约 95 MB 的 ONNX 权重并校验 SHA-256。缺少权重时应用显示「语义检索未启用」并使用关键词检索。

```powershell
uv run --locked python -m filemind --install-model
```

Windows x64 优先用 DirectML GPU 计算 BGE 向量，GPU 不可用时会回退到 CPU。「设置与任务」会显示当前推理设备。PDF、Office 和文本的解析由 CPU 执行。已有的向量在切换推理设备后可以继续使用。

### 带引用问答

问答默认展示带位置的原文摘录。使用兼容 Chat Completions 的服务时，在启动前设置 `FILEMIND_LLM_URL`（完整的 HTTPS 接口地址）、`FILEMIND_LLM_MODEL` 和 `FILEMIND_LLM_API_KEY`。直连 OpenAI 可用 `https://api.openai.com/v1/chat/completions` 和模型名 `gpt-6-luna`。参见 [OpenAI 官方模型文档](https://developers.openai.com/api/docs/models/gpt-6-luna)。

如果 FileMind 已在托盘运行，先通过托盘菜单退出，再从有配置的环境启动。打开「设置与任务」，勾选「允许远程问答」后，问答会向指定服务发送当前问题、限定范围内的相关摘录及引用编号。证据不足时会说明原因，并保留已找到的摘录。服务不可用或引用校验失败时也可查看原文摘录。

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
