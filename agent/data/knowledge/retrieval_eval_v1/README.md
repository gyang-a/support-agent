# 证据级 RAG 清洗与切片评测集 v2

本目录包含 50 份完全虚构的设备文档和 500 道证据级查询。所有参数只用于测试，
不得用于真实商品答复。型号成对设计为 hard negatives，避免只靠品类词完成召回。

## 文件

- `manifest.json`：入库元数据与源文件路径。
- `questions.json`：500 道问题、标准答案、正确文档、章节、PDF 页码、证据和相似型号负例。
- `qa_reference.md`：便于人工抽查的 500 道问题与标准答案清单。
- `cleaning_expectations.json`：清洗阶段应保留的标题、表格、列表、正文及应删除的页眉页脚。
- `documents/`：混合 DOCX、PDF 和 Markdown，共 50 份。

## 推荐指标

清洗评测：必需正文保留率、标题恢复率、表格恢复率、列表恢复率、页眉页脚残留率、
重复文本率和乱码率。切片评测：证据 Chunk Recall@1/3/5、MRR、nDCG@5、完整事实
同块率、跨章节污染率、相似型号泄漏率，以及 Top-K 证据覆盖率。

DOCX 的源格式没有稳定页码，所以 `page_start` 为 null，`expected_rendered_page` 只供
渲染核验。PDF 的 `page_start` 是可自动校验的真实页码。

## 运行检索评测

下面的命令复用项目正式的清洗、切块、Dense、BM25、RRF 和重排逻辑，不调用生成 LLM：

```powershell
.\.venv\Scripts\python.exe .\agent\scripts\evaluate_retrieval_eval_v1.py
```

默认同时执行“不使用型号过滤”和“使用完整元数据过滤”两轮，结果写入
`agent/runtime/retrieval_eval_v1_report.json` 与同名 Markdown 文件。可使用
`--max-questions 10` 快速试跑。
