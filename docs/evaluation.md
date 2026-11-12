# P0 检索评测

样例库含 25 份合成资料，PDF、DOCX、PPTX、MD、TXT 各 5 份。37 条查询中，32 条有预先标注的目标文件，5 条在样例中无答案。样例文本与标注保存在 [corpus.json](../samples/corpus.json)。脚本每次在临时资料库生成实际文件并重新导入。

Windows 11 x64（26200）、Intel Core i9-14900HX、Python 3.12.13、SQLite 3.53.1。语义和混合使用本机 CPU 上的 [BAAI BGE Small 中文模型](https://huggingface.co/BAAI/bge-small-zh-v1.5) 的 [固定版本 ONNX 转换](https://huggingface.co/Xenova/bge-small-zh-v1.5/tree/75c43b069aac4d136ba6bc1122f995fedcfd2781/onnx)。

| 模式 | 有答案查询 Hit@5 | 文件级 MRR | 预热后 p50 | 预热后 p95 |
| --- | ---: | ---: | ---: | ---: |
| 关键词 | 30/32（93.75%） | 0.922 | 3.5 ms | 5.5 ms |
| 语义 | 31/32（96.88%） | 0.933 | 20.6 ms | 40.2 ms |
| 混合 | 31/32（96.88%） | 0.935 | 20.4 ms | 22.8 ms |

MRR 使用每条有答案查询的首个正确文件排名，漏检按零计。延迟在每种模式预热一次后记录 37 条查询，包含检索、文件状态检查与结果读取。25 份文件导入和文本索引耗时约 5.2 秒。语义首次查询（加载权重并生成全部向量）约 0.98 秒，混合复用了已生成的向量。

中文问题「怎样记录从异地副本取回资料花了多久」的目标是英文 `data_backup.pdf`。关键词未检出，语义排第 6，混合排第 10。

## 无答案问题

下表记录各模式返回的候选文件数。语义与混合按相关度返回候选，仍需由用户或问答服务判断内容是否回答了问题。

| 查询 | 关键词 | 语义 | 混合 |
| --- | ---: | ---: | ---: |
| 系外行星光谱观测仪器 | 0 | 25 | 25 |
| 手冲咖啡的滤纸规格 | 0 | 25 | 25 |
| 北海道冬季铁路班次 | 0 | 25 | 25 |
| 陶瓷釉料的烧制温度 | 0 | 25 | 25 |
| TLS 证书颁发机构的价格是多少 | 1 | 25 | 25 |

## 复现

```powershell
uv run --locked python -m filemind --install-model --data-dir .\data\p0
uv run --locked python -m samples.evaluate --model .\data\p0\models\bge-small-zh-v1.5.onnx --output .\data\p0\evaluation.json
```

JSON 报告记录每条查询的前五结果、目标排名和无答案候选数。缺少模型时报告跳过语义与混合测量。此次实测运行了三种模式。远程问答使用本地模拟接口验证，未调用第三方模型服务。
