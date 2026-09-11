# Docker 部署

单台 Linux ECS 使用 Docker Compose v2。Nginx 托管前端，`/api/` 同源代理到 FastAPI；MySQL 保存账号、登录态和会话，Milvus 使用 etcd + MinIO。Embedding 与重排均调用硅基流动，无 Ollama、GPU 或 Hugging Face 权重下载。

## 1. 配置

在仓库根目录执行：

```bash
cp .env.example .env
```

填写 `DEEPSEEK_API_KEY`、`SILICONFLOW_API_KEY`、MySQL 和 MinIO 密码。不要提交 `.env`。Compose 读取根目录 `.env`；本地 Python 开发仍读取 `agent/.env`，两者用途不同。

`MYSQL_PASSWORD` 会插入数据库 URL，请使用随机十六进制密码，避免 `@`、`:`、`/` 等 URL 特殊字符。可分别执行三次 `openssl rand -hex 24` 生成不同密码。MySQL 密码初始化仅在空卷首次启动生效；已有数据库改密码不能只修改 `.env`。

硅基流动配置已写入 Compose：

| 用途 | 模型 | 接口 |
|---|---|---|
| Embedding | `Qwen/Qwen3-Embedding-0.6B`，1024 维 | `https://api.siliconflow.cn/v1/embeddings` |
| 重排 | `BAAI/bge-reranker-v2-m3` | `https://api.siliconflow.cn/v1/rerank` |

Key 仅传给后端与内部 MCP 子进程，前端不含 Key。API 重排失败时按现有规则降级，日志标明 `api_unavailable`；不会尝试下载本地模型。

## 2. 构建和启动

先按下一节将开发数据快照复制到服务器的 `deployment-data/snapshot/`，再启动。
`data-init` 是恢复数据的一次性任务，完成后退出；后端依赖它成功结束，不增加常驻模型服务。
快照缺失、文件损坏、目标已有未登记的数据或恢复校验失败，都会阻止首次后端启动。

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs data-init
docker compose logs --tail=100 backend
```

访问 `http://服务器当前IP`。首次进入点击“创建账号”，输入用户名和密码即可注册并登录。用户名为 3–32 位英文字母、数字或下划线，不区分大小写；密码为 8–128 位字符。

仅 Nginx 发布端口（默认 80，可用 `HTTP_PORT` 修改）；5000、3306、19530、2379、9000/9001 不对公网开放。ECS 安全组放行网页端口，SSH 按自己的管理来源限制。公网账号访问建议配置 HTTPS，随后将 `AUTH_COOKIE_SECURE=true` 并重建后端；当前 HTTP 演示配置使用 `false`。

基础镜像首次需要下载；国内网络不稳定时可提前在可联网机器构建镜像，再推送自己的阿里云镜像仓库或通过 `docker save/load` 导入。不要用未知公共镜像替换数据库镜像。后端构建使用 `agent/requirements.lock` 约束已构建验证的 Linux Python 3.11 依赖版本；本地 Windows 开发继续使用 `requirements.txt`。升级依赖时需重新验证并更新约束文件。

## 3. 将开发数据带到服务器

已生成的 `deployment-data/snapshot/` 包含开发 MySQL 的全部 12 张业务表及 Milvus 的全部 8 个集合。
其中有 200 个商品、202 条报价、8 个工作台会话、14 条运行记录、账号/登录态、消息和 Checkpoint。
Milvus 包含旧知识、评测集合、长期记忆、缓存及迁移后的集合；空缓存也保留结构和索引。
当前知识集合 `digital_technical_knowledge_v3_1024_sf_qwen3_06b_v1` 已复制旧集合的 440 条记录，保留原主键、1024 维向量和元数据，没有重新调用 Embedding。
原知识集合和迁移前目标集合的 10 条策略均保留，后者名称带 `_before_migration_20260911`。

快照约 71 MiB，包含账号哈希、登录态及对话数据，已排除在 Git 和 Docker 构建上下文外。因此只上传代码或镜像不会携带数据库，**必须额外复制快照目录**：

```powershell
# 本地执行；把占位符替换为自己的 SSH 用户、地址和项目路径
ssh <SSH用户>@<服务器IP> "mkdir -p /opt/support-agent/deployment-data"
scp -r deployment-data/snapshot <SSH用户>@<服务器IP>:/opt/support-agent/deployment-data/
```

服务器项目根目录应存在 `deployment-data/snapshot/manifest.json` 及其引用的所有 JSON 文件。
然后执行第二节的 `docker compose up -d --build`。日志出现
`MySQL + Milvus restore and full content verification complete` 表示首次恢复完成。
脚本逐文件检查 SHA-256，恢复后逐表、逐集合比较完整记录内容与条数；BM25 稀疏索引由保存的原文重建，密集向量直接恢复。
保留 AutoID 使用 Milvus 的 [allow_insert_auto_id](https://milvus.io/docs/modify-collection.md) 能力，导入后关闭该临时开关。

恢复标记保存在 MySQL `_deployment_snapshot`。同一快照再次运行会保留上线后新增/删除的数据；中断的恢复可用原快照重试，发现内容冲突即停止。
更换快照不会自动覆盖已上线数据库。首次恢复要求空 MySQL 和空 Milvus；已有云端数据需要另行制定合并方案，不要为通过检查删除现有卷。

若开发环境数据又变了，先停止本地后端及所有写库脚本（数据库保持运行），再导出到一个**尚不存在**的目录：

```powershell
.venv/Scripts/python.exe agent/scripts/deployment_data.py export deployment-data/snapshot-next
```

MySQL 使用一致性事务快照；MySQL 和 Milvus 没有跨服务事务，因此导出时需暂停应用写入。
确认导出成功后，把新目录作为服务器首次部署的 `deployment-data/snapshot`。当前导出器支持本项目的 InnoDB 表和默认分区集合，遇到视图、触发器、动态字段或自定义分区会拒绝，不会静默丢弃。

部署后用真实查询核对 API 和检索链路：

```bash
docker compose exec backend python agent/scripts/search_knowledge.py "AirSense Pro 支持日志存储和导出吗" --document-type product_manual
```

同名模型和维度相同不保证两个供应端的预处理、量化完全一致；此次按演示要求直接复用旧向量。已实测上述日志问题：纯向量 Top 3 均召回 AirSense Pro，最高余弦相似度约 0.8195；混合检索后硅基流动重排最高约 0.9933。该单条验证不等于完整召回评测。查询向量及重排仍需可用的硅基流动账号，搬数据不会解决 API 余额不足。

已注册账号保留原有会话归属。旧 `user_id=user_1001` 工作台记录保留在数据库中，不会自动分配给新账号。新注册账号仍拥有自己的会话；业务工具继续固定使用 `user_1001`。

## 4. 身份边界

| 数据/功能 | 身份 |
|---|---|
| 工作台会话、执行记录、读取/重命名/取消权限 | 当前登录账号 |
| Checkpoint、消息审计、长期偏好 | 当前登录账号 |
| Agent 状态和订单/物流/售后工具 | 固定 `user_1001` |

登录使用 MySQL Session 与 HttpOnly、SameSite Cookie。写接口要求 `X-Requested-With: SupportAgent`，浏览器前端自动发送，并检查 Origin 与当前 Host 相同。旧 `/api/chat` 也需要登录，请求中的 `user_id` 不决定业务身份；其会话 ID 带独立前缀，不能绕过工作台权限读取其他会话。

登录/注册有 Nginx 基于来源 IP 的速率限制。演示业务数据共享，工单仍为 JSON，未改造成业务用户系统。

当前后端仅使用一个 worker：运行中任务、取消操作和后台偏好提取依赖进程内状态。不要直接增加 worker 或副本数。

## 5. 重启、IP 变化及更新

前端只请求 `/api/...`；Nginx 使用 Docker 服务名 `backend`，不写公网 IP。公网 IP 变化后打开新地址即可，不需要重新构建。Cookie 属于旧站点，换 IP 后重新登录。使用域名时更新 DNS；使用 HTTPS 时证书必须匹配访问域名。

Compose 服务配置 `restart: unless-stopped`，请确保 Docker 服务随系统启动。主机重启后账号、聊天记录及数据库保留；重启前未完成的任务会标为已取消。健康检查不调用收费模型，健康仅代表 API/MySQL 就绪；完整知识检索需要实际问答验证。

```bash
# 更新代码后重建镜像；已有数据卷保留
docker compose up -d --build

# 停止服务但保留数据
docker compose down
```

不要执行 `docker compose down -v`，它会删除本项目命名卷。卷位于 Docker 所在磁盘；释放实例或磁盘前仍需备份。

`agent-runtime` 保存轨迹，`agent-data` 保存演示 JSON。空卷首次从镜像初始化；更新镜像不会覆盖已有 `agent-data`，修改演示数据时需单独同步，避免覆盖已写入的工单。

## 6. 本地回归

```powershell
uv pip install --python .venv/Scripts/python.exe -r agent/requirements-test.txt
.venv/Scripts/python.exe -m pytest agent/test/test_auth_workspace.py agent/test/test_siliconflow_reranking.py agent/test/test_workspace_stream.py -q --basetemp=.tmp/pytest-auth
cd frontend
npm ci
npm run build
npm run test
npm run lint
```

账号测试使用隔离的 SQLite 临时数据库，不请求真实模型。云端仍使用 MySQL；部署后应使用两个账号验证注册、重新登录、各自会话及越权拒绝，再验证实际模型回答。
