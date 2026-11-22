# FileMind 桌面性能评测

2026-11-22，Windows 11 26200，8 个逻辑处理器，CPython 3.12.16，SQLite 3.53.1。使用同一台机器、同一 uv 环境和仓库所在的 X 盘。每轮使用新的临时资料库。测试数据由仓库生成。

## 正文解析与常驻索引

混合集含 120 份文件。PDF、DOCX、PPTX、XLSX 各 24 份，TXT 与 Markdown 各 12 份。每份文档有 1–8 段内容，语料约 1.77 MiB。另用 120 份 Markdown 测纯文本目录索引和监听增量。混合文档场景跑两轮，纯文本场景跑三轮，计时前预热分词器和 WordPiece 词表。两版按相同规则生成正文和位置，Office 生成时间等容器元数据可不同。

基线为 `c7d587f`。批量导入从提交文件列表到副本和正文索引完成。目录首次索引从登记目录到来源显示就绪。监听增量从向已完成初次扫描的空目录复制文件开始，直到所有文件在 SQLite 中标记为已索引。计时包含应用的文件校验、正文解析、分块和 SQLite 写入，向量模型未参与计时。

| 场景 | 优化前各轮（秒） | 优化后各轮（秒） | 中位耗时变化 |
| --- | ---: | ---: | ---: |
| 导入 120 份混合文档 | 51.565 / 50.182 | 6.758 / 6.444 | 50.873 → 6.601 秒（−87.0%） |
| 关联目录首次索引 120 份混合文档 | 46.202 / 41.275 | 3.987 / 3.963 | 43.739 → 3.975 秒（−90.9%） |
| 关联目录首次索引 120 份 Markdown | 5.635 / 4.651 / 4.590 | 2.632 / 2.543 / 2.437 | 4.651 → 2.543 秒（−45.3%） |
| 监听到 120 份新增 Markdown 并建立索引 | 8.331 / 8.532 / 8.945 | 5.440 / 5.743 / 5.986 | 8.532 → 5.743 秒（−32.7%） |

诊断时另外测了 30 份混合文档，其中一次旧版导入耗时 12.394 秒，解析调用累计 9.825 秒。复用隔离解析进程后，120 份混合文档的导入和首次索引中位耗时先降到 10.384 和 6.134 秒，纯 Markdown 的首次索引仍有较大波动。随后合并新文件的首次指纹和正文索引写入，每份新文件少一次独立事务。现代 Office 和 PDF 的子进程每次最多服务 32 份文件，应用仍按文件检查大小、路径、内容版本和超时。常驻索引有积压时缩短批次间等待。

每轮检查所有文件均为已索引，并比较按文件名排序的正文、来源位置、标题、块偏移和分词结果的 SHA-256。混合集在两个入口均得到 435 块，摘要为 `89db4b470d56957a2e3960ce7e5f123ecdd6c0fae17948b72f3db4bd16295c92`。Markdown 在两个场景均得到 120 块，摘要为 `e0381237947324c48538ab0e4b603b978d9e16256dbbbd8a09815429fd0ef0d5`。两组优化前后摘要一致。

另在优化版用本机真实 BGE 权重和 CPU，对同一套 120 个 Markdown 文本块单独测量。模型加载 0.613 秒，加载后生成并存储 120 个向量耗时 0.448 秒。该次测量未与旧版比较。

## PDF 阅读扩样

基线为上次优化之前的 `a558f1d`。样本为 12 份文本 PDF，文档页数从 2 到 40 页，总计 174 页。使用 Qt 离屏窗口，每轮打开每份 PDF 的首页并依次跳到末页、中间页和末页，跑两轮，共 24 次打开和 72 次翻页。计时从已读取的字节交给阅读器或触发跳页开始，到图像就绪并更新界面。每次核对图像、页码和总页数。

| 操作 | 中位耗时优化前 → 优化后 | p95 优化前 → 优化后 |
| --- | ---: | ---: |
| 打开首页（24 次） | 1,310.0 → 840.1 ms | 1,696.6 → 1,074.3 ms |
| 同一文档内翻页（72 次） | 1,354.2 → 183.3 ms | 1,668.6 → 215.3 ms |

## 回归验证

Ruff 检查和格式检查通过。使用真实 BGE 模型跑完 42 项 pytest，另在 Windows 原生 Qt 平台复跑 6 项窗口测试。新增回归覆盖多格式共用解析进程、损坏文件隔离、超时重建，以及首次索引写入失败时的事务回滚和重试。源码包和 wheel 构建成功。

## 复现

在含本评测脚本的仓库根目录，用 PowerShell 运行。保留当前工作树，使用 Git 归档提取基线源码。

```powershell
. .\scripts\dev-env.ps1
uv sync --locked
uv run --locked python -m samples.benchmark_desktop processing
uv run --locked python -m samples.benchmark_desktop pdf

$fileMindBenchmarkRoot = (Get-Location).Path
$fileMindBenchmarkBase = Join-Path $env:TEMP ("filemind-compare-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $fileMindBenchmarkBase | Out-Null
$fileMindSavedPythonPath = $env:PYTHONPATH
try {
    foreach ($fileMindTrial in @(@{ Commit = 'c7d587f'; Scenario = 'processing' },
                                 @{ Commit = 'a558f1d'; Scenario = 'pdf' })) {
        $fileMindTrialPath = Join-Path $fileMindBenchmarkBase $fileMindTrial.Commit
        git archive --format=zip --output="$fileMindTrialPath.zip" $fileMindTrial.Commit src pyproject.toml
        Expand-Archive -LiteralPath "$fileMindTrialPath.zip" -DestinationPath $fileMindTrialPath
        $env:PYTHONPATH = "$fileMindTrialPath/src;$fileMindBenchmarkRoot"
        uv run --locked python -m samples.benchmark_desktop $fileMindTrial.Scenario
    }
} finally {
    $env:PYTHONPATH = $fileMindSavedPythonPath
}
```

脚本以 JSON 输出每轮耗时、索引摘要、PDF 中位数与 p95。复测纯文本时，可在两版的 `processing` 命令后加 `--repeats 3 --operation markdown_watch --operation markdown_changes`。当前数据覆盖小型文档和文本型 PDF。图片密集 PDF、旧版 Office、网络盘及真实资料库上的向量补建吞吐未纳入本次对照。
