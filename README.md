# FileMind

FileMind 是《智能软件工程》课程项目，面向需要按内容检索、阅读和整理本机资料的个人用户。当前版本是 Windows 桌面开发版，提供文件库、智能检索、智能整理、设置与任务四个导航入口，以及本地存储和启动诊断。

## 在 Windows 上运行

在仓库根目录打开 PowerShell。开发环境使用 Windows x64、Python 3.12 和 uv。尚未安装 uv 时，可运行 `winget install --id astral-sh.uv --exact`，再重新打开 PowerShell。

```powershell
uv sync --locked
uv run --locked filemind
```

首次启动会在 `%LOCALAPPDATA%\FileMind` 下初始化 SQLite 数据库和受管工作区。设置页可以打开数据目录。应用会记住上次打开的页面。

如果需要隔离演示或测试数据，可以显式指定目录。

```powershell
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
- [变更日志](CHANGELOG.md)

文件导入、关联目录、索引、检索、问答和整理将在 P0/P1 接入。验收条件和当前状态见 [需求与验收](docs/requirements.md)。
