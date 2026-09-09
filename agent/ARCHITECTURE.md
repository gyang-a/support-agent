# 存储与知识架构

## 数据职责

| 组件 | 权威职责 | 明确不承担 |
|---|---|---|
| MySQL | 商品、渠道报价库存、LangGraph Checkpoint、会话消息、结构化用户偏好 | 语义文档召回 |
| Milvus | 设备说明、技术文档、售后政策向量检索，以及严格筛选的公开 FAQ 语义缓存 | 用户画像、价格、订单 |
| Neo4j | 设备、配件、接口、协议和兼容关系 | 会话、实时业务状态 |

## 请求链路

### 单任务与复合任务编排

```text
Router ─ 单个专业 Agent 可覆盖 ───────────────┐
  └─ 跨领域 → TaskPlannerAgent → 计划校验 ──┤
                                          ↓
                         TaskScheduler（按依赖并行调度）
                                          ↓
                       Synthesis（单任务直出，多任务汇总）
                                          ↓
                              QualityMonitor → END
```

Router 每轮检查全部意图，不再使用关键词命中后直接返回的短路。一个 Agent
内部的多次工具调用仍走单任务路径。规则分类函数保留给离线评估和解析兼容入口；
在线路由输出损坏时转到编排器，不用单个关键词静默丢弃其他需求。

编排器输出最多 8 个任务，每项包含 `task_id`、`agent_name`、`query`、
`depends_on`。执行前校验 Agent 白名单、唯一 ID、非空问题、依赖存在性和无环性。
非法计划或无法覆盖的业务请求不执行，返回澄清提示。第一版不动态重规划。

调度器默认最多同时执行 4 个任务，不设置整个专业任务的固定时长上限，避免
正常推进的复杂任务在 180 秒时被截断。需要业务总时限时可在构造 `TaskScheduler`
时显式指定 `timeout_seconds`；单次模型/Embedding 请求超时与服务熔断仍保留。
任何前置任务完成后立即释放其就绪下游，不等待无关任务完成。
每个调用持有独立 State 副本，保留可信 `user_id`、共享历史、用户记忆，
另注入当前子任务与 `depends_on` 指定的本轮结果。RAG 预检索使用子问题。

专业 Agent 通过 `ToolStrategy(SpecialistResponse)` 返回 `completed`、
`needs_input` 或 `failed`、用户答复以及必要结构化数据。包装层以计划中的
`task_id` 和 `agent_name` 标记来源，不让模型自行指定来源。前置任务未完成时，
依赖它的任务返回 `blocked`；独立任务继续。调度器不自动重试专业 Agent，避免
重复业务写操作；模型调用底层的原有网络重试机制保持不变。

主 State 的 `tasks` 保存计划，`task_results` 按任务 ID 合并；相同 ID 的新结果
替换旧结果。空更新不清除已有结果，Router 在每轮开始以 `None` 显式重置结果。
缓存命中也清空本轮任务字段。完整的历史 checkpoint 仍包含之前的任务结果。

`messages` 只追加用户原话与每轮最终答复，中间专业答复不进入共享会话。
专业 Agent 使用原有摘要机制，子任务和依赖数据放进调用提示词，不追加伪造的
用户轮次，因此摘要游标仍对应原始 `messages`。后续追问通过会话历史中的最终
答复、精确业务标识及滚动摘要承接；历史动态事实必须重新查询。

任务结果在调度节点返回时统一提交到 Checkpoint。当前不支持调度节点执行中的
逐任务持久化恢复；进程中断后不能保证已发生的业务写操作恰好执行一次。
生产写接口仍需业务幂等键，不能通过重放整个调度节点实现安全重试。

多任务汇总失败时直接组合各任务原始答复，保留未完成事项及追问；单任务不额外
调用汇总模型。质量监控按任务问题与工具轨迹评分，最终答复另外检查安全性。
SSE 的文本分片契约不变，最后的 `done` 事件增加 `task_results`，每项仅返回
`task_id`、`agent_name`、`status`、`response`，不返回内部工具数据和异常详情。

### 存储与调用

1. FastAPI 根据 `user_id + session_id` 生成不暴露身份的 SHA-256 `thread_id`。
2. 仅公开、稳定、非个性化且能识别技术实体的问题查询 Milvus 语义缓存。
3. 意图、实体集合、知识版本、TTL 和向量相似度全部通过后才命中；命中时跳过模型，但仍将问答写入 MySQL Checkpoint 和消息审计表。
4. 缓存未命中时，LangGraph 从 MySQL 恢复 thread 状态并执行专业 Agent。
5. 专业 Agent 的初始请求上下文超过 50k 估算 token 时，把较早消息增量压缩为不超过 500 字的会话摘要，并保留最近 20 个完整轮次；Agent 工具循环中的每次模型调用也执行 50k 检查，必要时清理较早工具结果或截断异常大的最新结果。原始消息和 Checkpoint 不删除。
6. 后台任务定期把未处理对话和已有偏好折叠为完整偏好快照并覆盖到 MySQL。
7. 技术和政策问题优先从 Milvus召回带版本、章节和来源的文档。
8. 兼容性问题优先查询 Neo4j；不可用时使用同版本审核 JSON 快照。
9. 商品 Agent 只调用固定 MCP 工具；工具通过 SQLAlchemy 在 MySQL 内完成过滤、排序和 `LIMIT`，不会把商品全表交给模型。

## MySQL 表

- `graph_checkpoints`：完整 LangGraph checkpoint blob。
- `graph_checkpoint_writes`：节点 pending writes，用于中断恢复。
- `conversation_sessions`：可查询的业务会话。
- `conversation_messages`：用户/助手消息与 Agent、Trace 元数据。
- `user_preferences`：后台任务整体覆盖的完整用户偏好快照。
- `preference_extraction_cursors`：每个用户已成功处理的最后消息 ID。
- `products`：SKU、品类、品牌、型号、规格、用途标签和数据来源。
- `product_offers`：SKU 在地区与渠道维度上的价格和库存快照。

SQLAlchemy 在启动时执行幂等 `create_all`。数据库本身可用
`docker/mysql/init/001_create_database.sql` 创建。

## 缓存安全

Milvus 语义答案缓存默认拒绝订单、物流、工单、用户偏好、推荐、预算、价格、
库存、优惠、购买、售后和高风险故障问题。查询与候选答案必须具有相同意图和
规范化实体集合，并满足知识版本、TTL 与高相似度阈值。可共享 FAQ 在不注入
用户画像的条件下生成，缓存内容可随时删除并重新生成。


## 记忆架构

进入专业 Agent
│
├─ 组装并估算系统提示词、工具定义、长期记忆和共享消息
│
├─ 未超过50k
│  └─ 使用当前完整模型视图
│
└─ 超过50k
   ├─ 保留最近20个完整轮次
   ├─ 更早消息按约30k token 分批滚动压缩
   └─ 生成不超过500字符的会话摘要

模型调用工具
→ AIMessage(tool_calls)
→ ToolMessage(result)
→ 下一次模型调用前重新检查50k预算

超过预算：
1. 优先清理靠前的旧 ToolMessage 内容；
2. 保留对应的 AIMessage.tool_calls，维持调用关系；
3. 尽量完整保留最新 ToolMessage；
4. 最新结果本身过大时，保留部分头尾并加入截断说明。
