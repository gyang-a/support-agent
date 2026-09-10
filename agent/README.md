# 数码商城多智能体后端

当前后端包含商品咨询、选购推荐、配件兼容性、订单物流和售后诊断 Agent。
第三阶段新增售后政策检索、规则化故障预诊断、工单与人工升级、
运行追踪和质量评测监控。三栏客服前端位于项目根目录的 `frontend/`，
使用方法见 [前端说明](../frontend/README.md)。

支持单轮多意图：Router 判断单领域或复合请求，编排 Agent 拆解跨领域任务，
调度器并行执行独立任务并按依赖启动下游，最终统一答复。专业 Agent 保留共享
历史和用户记忆，中间结果独立保存；SSE 完成事件返回任务状态及 `agent_name`。
详细流程、状态契约和恢复边界见 [ARCHITECTURE.md](ARCHITECTURE.md)。

不调用真实模型或数据库的编排回归测试：

```powershell
.\.venv\Scripts\python.exe -m pytest .\agent\test\test_task_orchestration.py -q
```

## Windows 环境安装

```powershell
uv venv --clear .venv --python 3.11
uv pip install --python .\.venv\Scripts\python.exe -r .\agent\requirements.txt
Copy-Item .\agent\.env.example .\agent\.env
```

在 `agent/.env` 中填写 `DEEPSEEK_API_KEY`。当前模型配置使用 DeepSeek 的
OpenAI 兼容接口，Agent 编排使用 `langchain.agents.create_agent`。

MySQL 是商品、价格库存、会话、LangGraph Checkpoint 和用户偏好的权威存储。先创建
`digital_agent` 数据库，再配置 `MYSQL_URL`。详细边界见
[ARCHITECTURE.md](ARCHITECTURE.md)。

完整会话始终保存在 Checkpoint 和消息审计表中。专业 Agent 的初始上下文超过
50k 估算 token 时，模型视图会切换为不超过 500 字的滚动摘要加最近 20 个
完整对话轮次；摘要使用游标增量更新，不会删除或覆盖原始消息。Agent 内部
每次模型调用前也会检查同一预算，必要时只清理本轮较早的工具结果；单个最新
工具结果异常巨大时会保留尽可能多的头尾内容并明确标注截断。

Milvus用于设备说明、技术文档、售后政策的语义检索，以及公开稳定 FAQ 的语义缓存。需要启用时，在 `.env`
中配置 `EMBEDDING_API_KEY`、`EMBEDDING_MODEL`、`EMBEDDING_BASE_URL` 和对应
向量维度，同时启动 Milvus。Neo4j用于设备、配件、接口、协议和兼容关系；
不可用时兼容查询会降级到同版本的审核 JSON 快照。

## 知识文档预处理

当前支持 PDF、DOCX 和 Markdown 的正文提取、保守清洗与结构恢复，输出统一的
JSON 中间结构。PDF 由 PyMuPDF 恢复 word/span、字体和坐标版面，由 pdfplumber
增强有边框表格，并以“粗体表头 + 多列对齐 + 连续数据行”恢复无边框表格；同时
删除高置信的重复页眉页脚、保留真实页码范围并恢复标题与段落。DOCX 保留标题样式、
列表和表格，但不会伪造由渲染器决定的页码；扫描 PDF 暂不支持 OCR。

```powershell
.\.venv\Scripts\python.exe .\agent\scripts\preprocess_knowledge_document.py `
  .\knowledge\product_manuals\iphone_16.pdf `
  --output .\knowledge\parsed\iphone_16.json `
  --document-id iphone_16_manual
```

输出块包含 `block_type`、`heading_path`、`page_start/page_end`、原始顺序、
规范化正文和表格行列结构，可直接作为语义切片的输入。

入库时优先按章节、段落和完整表格组织约 650 token 的切片，最大约 900 token，
相邻正文保留约 100 token 的语义块重叠。超长正文使用 LangChain
`RecursiveCharacterTextSplitter` 兜底；超长表格按完整行切分，每块重复标题、
表头和原始行号。切片正文、页码、章节、型号、类目和版本均直接写入 Milvus。

```powershell
.\.venv\Scripts\python.exe .\agent\scripts\index_knowledge_document.py `
  .\knowledge\product_manuals\iphone_16.pdf `
  --document-id iphone_16_manual `
  --document-type product_manual `
  --product-model iphone_16 `
  --category smartphone `
  --version 2026.08
```

检索并行执行 Embedding 稠密向量搜索和使用中文 `jieba` analyzer 的 BM25，
由 Milvus RRF 合并候选，再根据精确术语、标题、章节和重复内容进行二阶段重排。
可用下面的命令直接检查证据、分数和引用：

```powershell
.\.venv\Scripts\python.exe .\agent\scripts\search_knowledge.py `
  "iPhone 16 使用 USB-C 充电有什么要求" `
  --document-type product_manual `
  --product-model iphone_16 `
  --category smartphone
```

命令默认返回 3 个候选并只展示 `excerpt`；使用 `--limit 1` 只看首条证据，
需要排查完整切片时可增加 `--include-content`。

### RAG 检索基准

`data/knowledge/benchmark` 提供 6 份合成 Markdown 文档和 18 道标准问答，专门
覆盖相似型号串库、章节命中、跨章节证据、表格行、功率参数和安全禁忌。所有
参数仅供测试，不代表真实商品规格。即使 Milvus 未启动，也可以使用相同切块、
Embedding、BM25、RRF 和二阶段重排逻辑运行离线基准：

```powershell
.\.venv\Scripts\python.exe .\agent\scripts\evaluate_rag_benchmark.py
```

结果写入 `runtime/rag_benchmark_report.json` 和同名 Markdown，分别报告 Dense、
BM25、混合 RRF 和最终重排阶段的 Top-1、Hit@3、Recall@3、MRR、nDCG@3、
关键事实覆盖率以及型号过滤泄漏率。

Milvus 启动后，下面的在线基准会真实执行“解析 → 切块 → Embedding → 写入
Milvus → Dense+BM25 → RRF → 二阶段重排”，并生成独立在线报告：

```powershell
.\.venv\Scripts\python.exe .\agent\scripts\evaluate_rag_benchmark_online.py
```

## 自动化测试

### 检索故障熔断

成功检索也会检测本次专业任务是否仍在获得新证据：预检索与后续工具调用共享
缓存，相同工具和参数直接复用结果；连续两次检索没有新增文档片段时，移除该
检索工具，提示 Agent 用已有证据作答，证据不足则明确追问或说明无法确认。
改写查询、重排分数变化不算新证据；检索到新片段会清零连续无进展计数。
这不限制复杂任务的总调用次数，也不影响其他业务工具继续工作。运行日志中的
`RAG progress` 显示工具名、缓存复用、新证据数量及是否停止检索。

Ollama / Embedding 服务不可用时，单次向量化操作默认最多尝试 3 次（包含首次），
每次超时 5 秒，SDK 自带重试关闭。耗尽后同一进程内的知识检索和语义缓存共享
60 秒熔断窗口，期间直接失败，不再请求该向量服务；冷却后只允许一次恢复探测，
探测成功恢复正常，失败则重新冷却。知识库初始化失败也进入冷却，避免每次工具
调用都重连。独立 MCP 子进程各自维护熔断状态。

技术知识预检索不可用时直接结束当前专业任务；Agent 工具循环中收到
`knowledge_unavailable` 后也直接返回 `failed`，不会再调用模型尝试检索。
正常空检索结果仍返回 `not_found`，不误判成服务故障。售后通用政策保留本地审核
知识库降级；其他独立任务不受影响。

不按累计模型调用次数终止专业任务，也不默认限制整个任务的执行时长。
失败重试上限只约束 Embedding 故障，不影响正常推进的复杂任务。
熔断参数可通过 `EMBEDDING_MAX_ATTEMPTS`、`EMBEDDING_TIMEOUT`、
`EMBEDDING_CIRCUIT_COOLDOWN` 配置；原 `AGENT_MAX_MODEL_CALLS` 配置已移除。
大批量文档入库或模型冷启动较慢时，可适当增大 `EMBEDDING_TIMEOUT`。

不激活虚拟环境也可以运行：

```powershell
.\.venv\Scripts\python.exe -m pytest .\agent\test -q
```

预期结果为全部通过。商品领域测试会查询已配置的 MySQL；不会请求 DeepSeek、
Milvus 或 Neo4j。

首次配置数据库后，可重复执行下面的命令写入 200 条测试商品。脚本只替换
`source_name=synthetic_seed` 的记录，不会删除以后导入的真实商品：

```powershell
.\.venv\Scripts\python.exe .\agent\scripts\seed_synthetic_products.py --count 200
```

## 手工验收

```powershell
.\.venv\Scripts\python.exe .\agent\main.py
```

建议依次测试：

1. `对比 iPhone 16 和 iPhone 16 Pro。`
2. `iPhone 16 Pro 能用 Lightning 数据线吗？`
3. `65W USB-C 笔记本电源能给拯救者 Y7000P 用吗？`
4. `查一下订单 DG-1001-0002 的最新物流。`
5. `预算 5500 元，推荐一台适合编程的笔记本。`
6. `手机电池鼓包而且有异味，应该怎么办？`
7. `激活后的手机还能七天无理由退货吗？`
8. `查询我的售后工单。`

演示登录用户使用 `user_1001` 时可以查询 `DG-1001-0001` 和
`DG-1001-0002`；使用其他用户查询这些订单应返回无结果。

## 测试数据

- `products` 与 `product_offers`：运行时商品、价格和库存均从 MySQL 按需查询。
- `data/digital_catalog.json` 与 `data/digital_market.json`：只供种子脚本生成测试商品，运行时不读取。
- `data/digital_logistics.json`：物流时间线，每次工具调用重新读取。
- `data/compatibility_graph.json`：商品组、配件与兼容关系。
- `data/after_sales_policies.json`：带文档 ID 和生效日期的售后政策知识库。
- `data/diagnostic_rules.json`：经过安全约束的设备故障诊断规则。
- `data/digital_tickets.json`：本地演示工单；生产环境应替换为工单数据库。

生产环境接入价格中心、库存中心、物流接口或 Neo4j 时，替换对应 `core`
领域模块即可，MCP 工具与 Agent 调用契约无需改变。

## 质量评测与监控

每轮实际对话会在 `agent/runtime` 生成脱敏 JSONL 轨迹。内容包含路由、工具
调用、耗时、事实依赖、安全评分和低分原因；低分结果会另外写入告警文件。

生成固定测试集和已有运行轨迹的汇总报告：

```powershell
.\.venv\Scripts\python.exe .\agent\evaluate.py
```

固定测试集报告不调用 DeepSeek，也不会创建工单或修改订单。运行时轨迹中的
`user_id` 会哈希处理，显式用户标识、密码和验证码会在落盘前脱敏。
