# Support Agent · 智能客服

面向数码商城的多智能体客服后端，基于 LangChain、LangGraph 和 FastAPI，支持商品咨询、选购推荐、订单物流与售后服务。

现已包含三栏客服工作台：会话管理、聊天回答、Agent 执行轨迹与检索证据。前端采用 Vite、React、TypeScript、Zustand、Tailwind CSS 和 shadcn/ui，启动与架构说明见 [前端 README](frontend/README.md)。

后端启动后，在另一个终端执行：

```powershell
cd frontend
npm ci
npm run dev
```

访问 http://127.0.0.1:5173。工作台需要 MySQL 持久化。用户名、密码注册登录后，各账号独立保存会话、聊天记录和偏好；订单、物流等业务统一使用 `user_1001` 演示身份。

云服务器 Docker / Nginx 部署步骤见 [部署说明](docker/DEPLOYMENT.md)。Embedding 和重排均使用硅基流动 API，不需要部署模型容器或下载权重。

部署需要额外上传 `deployment-data/snapshot/` 开发数据库快照（已排除在 Git 和镜像外）。Compose 会先恢复、校验 MySQL 与 Milvus，再启动后端；旧 1024 维知识向量已经直接迁入当前集合，无需重新向量化。

## 核心能力

- **多意图编排**：识别复合请求，拆解任务，并行执行独立任务，按依赖调度后续任务，最终汇总答复。
- **专业 Agent**：商品咨询、选购推荐、兼容性查询、订单物流、售后诊断与工单处理。
- **知识库检索**：支持 PDF、DOCX、Markdown 文档处理，结合稠密向量、BM25、RRF 和二阶段重排。
- **会话与记忆**：使用 MySQL 保存会话、Checkpoint 和用户偏好，支持长对话上下文压缩。
- **流式接口**：提供 FastAPI / SSE 接口，返回任务状态与 `agent_name`。
- **故障处理**：Embedding 重试与熔断、检索无新增证据时收敛，以及运行追踪和质量评测。

## 项目结构

```text
support-agent/
├── agent/
│   ├── agents/          # 专业 Agent 与任务编排
│   ├── core/            # 领域服务、知识检索、会话与工作流
│   ├── config/          # 配置与 MCP 服务定义
│   ├── mcp_servers/     # 业务工具服务
│   ├── data/            # 演示数据与检索评测语料
│   ├── scripts/         # 数据初始化、文档入库与评测脚本
│   └── test/            # 自动化测试
├── app/                 # FastAPI 接口层
└── docker/              # Milvus 部署配置与 MySQL 初始化 SQL
```

## 快速开始

以下 PowerShell 命令均在项目根目录执行。需要 Python 3.11、uv，以及可用的 MySQL 服务。

```powershell
git clone git@github.com:gyang-a/support-agent.git
cd support-agent
uv venv .venv --python 3.11
uv pip install --python .\.venv\Scripts\python.exe -r .\agent\requirements.txt
Copy-Item .\agent\.env.example .\agent\.env
```

编辑 `agent/.env`，填写 `DEEPSEEK_API_KEY` 和 `MYSQL_URL`，并在 MySQL 中创建 `digital_agent` 数据库。初始化 SQL 见 [001_create_database.sql](docker/mysql/init/001_create_database.sql)。

写入演示商品：

```powershell
.\.venv\Scripts\python.exe .\agent\scripts\seed_synthetic_products.py --count 200
```

启动 API 服务：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.app_main:app --host 127.0.0.1 --port 5001
```

启动后访问 [API 文档](http://127.0.0.1:5001/docs)。也可以使用命令行交互入口：

```powershell
.\.venv\Scripts\python.exe .\agent\main.py
```

知识检索需要另行配置 Embedding 服务和 Milvus，并执行文档入库。部署配置见 [docker-compose.yml](docker/milvus/docker-compose.yml)，配置与入库步骤见 [后端使用说明](agent/README.md)。兼容关系支持 Neo4j，并提供本地 JSON 快照降级。

## 测试

无需调用真实模型或数据库的编排回归测试：

```powershell
.\.venv\Scripts\python.exe -m pytest .\agent\test\test_task_orchestration.py -q
```

完整测试及手工验收步骤见 [后端使用说明](agent/README.md#自动化测试)。部分测试需要已配置的 MySQL。

## 文档

- [后端使用说明](agent/README.md)：环境配置、文档处理、知识入库、评测与验收。
- [系统架构](agent/ARCHITECTURE.md)：多智能体编排、状态契约和上下文恢复边界。
- [检索评测语料](agent/data/knowledge/retrieval_eval_v1/README.md)：合成文档与标准问答。

仓库包含演示和合成评测数据，不代表真实商品规格。`.env`、虚拟环境、模型权重和数据库运行数据已通过 `.gitignore` 排除；部署时需自行配置。
