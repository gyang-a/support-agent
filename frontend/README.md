# Support Agent 前端

Vite + React + TypeScript + Zustand + Tailwind CSS + shadcn/ui。

## 启动

在项目根目录启动后端（需配置 agent/.env 和 MySQL）：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.app_main:app --host 127.0.0.1 --port 5000
```

另开终端：

```powershell
cd frontend
npm ci
npm run dev
```

打开 http://127.0.0.1:5173。Vite 将 /api 代理到后端 5000 端口。

## 分层

- app：入口、路由、全局设计变量。
- pages/workspace：三栏布局和模块组合。
- features/conversations：会话列表、搜索、重命名、服务端 API 和 Store。
- features/chat：输入草稿、消息列表、Markdown 和滚动行为。
- features/execution：执行契约、SSE 适配、请求生命周期、按 runId 隔离的 Store。
- features/inspector：任务树与检索证据，读取执行状态，不自行发起聊天。
- shared：shadcn 基础组件、协议级 SSE 客户端、界面偏好与通用组件。

对话内容仅在 executionStore 的 Run 中保存一份，chatStore 保存草稿。会话由 URL 定位，界面偏好持久化到 localStorage；历史回答和事件通过 MySQL workspace_conversations / workspace_runs 保存。现有 conversation_messages 和 Checkpoint 仍由原后端维护。

## 当前接口边界

- 工作台固定使用服务端演示用户 user_1001，不是生产登录系统。
- 任务规划、开始、完成事件来自实际调度器。
- 工具详情是任务执行后的名称及状态摘要，不含原始参数。
- 证据展示技术知识和售后政策预检索上下文，以及 Agent 执行中检索工具返回的片段（含任务内缓存复用）。按检索调用分别展示，不代表全部召回候选或回答最终引用。
- 当前原后端仍在整轮推理完成后发送回答切片，不是模型逐 token 输出。
- 停止按钮取消后台协程和未完成子任务；已经发生的业务操作不会被撤销。
- 单进程运行；活动执行映射位于当前进程，多实例需改为共享任务控制。
- 服务中断时，运行中的记录会在下次启动标记为中断。未完成阶段只保证初始记录落库，完整事件在执行结束/取消时保存。
- 新工作台历史从本次新增表开始记录，旧 conversation_messages 不自动迁入。
- Milvus 不可用时不生成演示证据；界面显示真实空状态或任务失败。

## 验证

```bash
npm run build
npm run lint
npm test
```

部署时用 Nginx 托管 dist，并将 /api 转发到 FastAPI；/chat/* 路径回退 index.html。SSE 关闭代理缓冲。API Key 只配置在后端，不能使用 VITE_ 前缀传到浏览器。
