# 架构说明

当前版本采用 Python 3.12、Qt Widgets 和 SQLite。架构决定见 [Windows 桌面方案](adr/0001-windows-desktop.md)。

```mermaid
flowchart LR
    Entry["启动与诊断"] --> Window["桌面窗口"]
    Window --> Task["后台任务"]
    Task --> Library["文件库"]
    Task --> Search["检索"]
    Task --> QA["引用问答"]
    QA --> Search
    QA --> Remote["已授权的问答服务"]
    Library --> Parser["解析与分块"]
    Library --> Files["受管副本和只读目录"]
    Library --> Store["SQLite 和 FTS5"]
    Search --> Store
    Search --> Model["本地 BGE"]
```

| 模块 | 当前职责 |
| --- | --- |
| `filemind.__main__` | 启动、诊断、下载模型及数据目录锁 |
| `filemind.ui` | 桌面交互、索引任务、检索结果、引用和纯文本预览 |
| `filemind.config` | 默认应用数据路径与显式 `--data-dir` |
| `filemind.library` | 保存受管副本、检查来源权限、扫描、重试及原文校验 |
| `filemind.documents` 和 `filemind.text_index` | 格式解析、位置与原文保留、WordPiece 分块及 jieba 分词 |
| `filemind.storage` | 数据库迁移、资料库状态、原文、FTS5 和向量 |
| `filemind.search` 和 `filemind.embeddings` | 文件级 BM25、余弦相似度和 RRF 排序 |
| `filemind.qa` | 限定证据、调用问答服务并核验引用 |

SQLite 结构版本 2 从版本 1 事务迁移，保留已有偏好。每个数据目录同一时刻由一个桌面进程操作。窗口内串行安排后台任务，导航和状态展示留在界面线程。

## 文件与索引

- 导入先记录待接入文件，再在独立子目录保存同名副本。复制完成后启动索引，重启可继续处理中断的任务。关联目录保存授权的绝对路径，目录取消关联时仅清除应用内的记录和索引。
- 启动和手动刷新读取文件快照，使用 SHA-256 识别内容变化。同一路径更新内容时保留文件 ID 并增加内容版本。已确认变化的文件暂时退出检索，解析成功后事务提交原文、分块和 FTS5 索引。来源扫描完整时才清理缺失文件的索引。
- 每个文件上限 32 MiB。PDF 与 Office 解析由独立子进程执行，时限 25 秒。Office 部件总解压上限 128 MiB，单个 XML 上限 8 MiB。PDF 和 PPTX 最多 1000 页或张，提取文本最多 200 万字符。扫描每个关联目录最多检查 2 万个项目。跳过应用数据目录、链接、重解析点和常见敏感配置名称。
- 原文按 PDF 物理页、PPTX 幻灯片、DOCX 正文与表格、文本行保存。使用 BGE 的 WordPiece 词表切为最多 384 token 的块并保留原文偏移。FTS5 单独保存分词后的术语，`FIRST/FOLLOW`、`B+`、`C++` 和扩展名以完整词项匹配。

## 检索与问答

- 关键词分支用 BM25，语义分支用本地 BGE 的 CLS 向量和余弦相似度。向量首次使用时分批计算，按模型指纹缓存。两分支先按文件取最相关文本块，混合模式按文件排名做 RRF。来源、格式和文件范围在两分支使用同一组过滤条件。
- 检索检查候选文件的状态，预览和引用核对当前 SHA-256、内容版本和文本。启用远程问答后，服务最多发送 6 条摘录，每条不超过 900 字符。返回的引用须匹配发送的块 ID、内容版本和逐字摘录。问答调用只具备文本输入和文本输出。
