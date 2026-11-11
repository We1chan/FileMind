# 开发与版本管理

## 安装与验证

在 Windows x64 的 PowerShell 中进入仓库根目录。开发解释器由 `.python-version` 指定，依赖版本由 `uv.lock` 锁定。

```powershell
uv sync --locked
uv run --locked filemind
uv run --locked python -m filemind --check --data-dir .\data\check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest
uv build --no-sources
```

桌面测试默认使用 Qt 离屏平台，可在 Windows 交互会话中用原生插件复测。

```powershell
$env:QT_QPA_PLATFORM = 'windows'
uv run --locked pytest tests/test_window.py
```

`uv build` 在 `dist/` 生成源码包和 Python wheel。GitHub Actions 对发往 `main` 的变更运行 Windows 检查。更新依赖时提交 `pyproject.toml` 和 `uv.lock`，并使用锁文件重新验证。

## 分支、提交与发布

- `main` 是集成分支。功能用 `feat/<主题>`，修复用 `fix/<主题>`。每个提交描述一项可验证的变更。变更合入前核对需求、测试和相关文档。
- 应用版本以 `pyproject.toml` 的 `project.version` 为准。开发版使用 `0.1.0.devN` 形式。正式发布按语义版本管理，更新版本号和 [变更日志](../CHANGELOG.md)，通过检查后为发布提交创建 `v<版本号>` 标签。
- 记录影响使用者的变化和实际验收情况。需求变更更新 [需求与验收](requirements.md)，架构选择记录在 [文档索引](README.md) 的架构决定中。
- 仓库只追踪源码、工程配置、无隐私样例和文档。应用数据、虚拟环境、模型、密钥与本地产物由 Git 忽略规则管理。
