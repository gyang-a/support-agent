from contextlib import asynccontextmanager
import sys

# Windows redirected terminals can default to GBK; Agent log symbols must not abort a request.
for output in (sys.stdout, sys.stderr):
    if hasattr(output, "reconfigure"):
        output.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from urllib.parse import urlsplit
from sqlalchemy import text

from app.router import chat
from app.router import workspace
from app.router import auth
from app.service.chat_service import close_agent_system, init_agent_system

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_agent_system()
        from app.service.workspace_store import recover_interrupted_runs, sessions
        sessions()  # Login/workspaces require MySQL; restart instead of serving a broken app.
        await recover_interrupted_runs()
        yield
    finally:
        await close_agent_system()

app = FastAPI(title="Multi-Agent Digital Commerce API", lifespan=lifespan)

@app.middleware("http")
async def same_origin_api(request: Request, call_next):
    # Browser writes must be same-origin and carry a non-simple request header.
    # Vite and Nginx preserve the browser Host; no fixed public IP is configured.
    if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if (request.headers.get("x-requested-with") != "SupportAgent"
                or (origin and urlsplit(origin).netloc != request.headers.get("host"))):
            return JSONResponse(status_code=403, content={"detail": "请从本站页面提交请求"})
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/healthz", include_in_schema=False)
async def healthz():
    from app.service.workspace_store import sessions
    async with sessions()() as db:
        await db.execute(text("SELECT 1"))
    return {"status": "ok"}

# 注册路由
app.include_router(chat.router, prefix="/api")
app.include_router(workspace.router, prefix="/api")
app.include_router(auth.router, prefix="/api")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.app_main:app", host="0.0.0.0", port=5000, reload=True)
