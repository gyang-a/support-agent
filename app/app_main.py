from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.router import chat
from app.service.chat_service import close_agent_system, init_agent_system

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化
    await init_agent_system()
    yield
    await close_agent_system()

app = FastAPI(title="Multi-Agent Digital Commerce API", lifespan=lifespan)

# 配置跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(chat.router, prefix="/api")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.app_main:app", host="0.0.0.0", port=5000, reload=True)
