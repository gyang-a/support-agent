from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.schemas.chat import ChatRequest
from app.service.chat_service import stream_chat
from app.service.auth import User, current_user
from app.service.workspace_store import DEMO_USER

router = APIRouter()


@router.post("/chat")
async def chat_endpoint(request: ChatRequest, user: User = Depends(current_user)):
    """
    处理多智能体聊天请求，并使用 SSE (Server-Sent Events) 返回流式响应。
    所有请求进入 Agent 图编排，确保动态价格、库存、订单和售后状态来自
    本轮受权限保护的工具调用。
    """
    return StreamingResponse(
        stream_chat(request.query, DEMO_USER, "legacy:" + request.session_id, account_id=user.id),
        media_type="text/event-stream",
    )
