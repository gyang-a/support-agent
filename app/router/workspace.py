"""Account-owned workspaces with a separate fixed commerce demo identity."""
import uuid
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.service import workspace_store as store
from app.service.workspace_stream import active, active_conversations, execute_stream
from app.service.auth import User, current_user

router = APIRouter(prefix="/workspace", tags=["workspace"])


class ConversationInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class RunInput(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    query: str = Field(min_length=1, max_length=4000)


@router.get("/conversations")
async def conversations(user: User = Depends(current_user)):
    async with store.sessions()() as session:
        rows = (await session.execute(select(store.WorkspaceConversation).where(store.WorkspaceConversation.user_id == user.id).order_by(store.WorkspaceConversation.updated_at.desc()))).scalars()
        return [store.serialize(row) for row in rows]


@router.post("/conversations")
async def create_conversation(body: ConversationInput, user: User = Depends(current_user)):
    row = store.WorkspaceConversation(id=str(uuid.uuid4()), user_id=user.id, title=body.title.strip() or "新对话", created_at=store.now(), updated_at=store.now())
    async with store.sessions()() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return store.serialize(row)


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(conversation_id: uuid.UUID, body: ConversationInput, user: User = Depends(current_user)):
    async with store.sessions()() as session:
        row = await store.owned(session, str(conversation_id), user.id)
        row.title = body.title.strip() or "新对话"
        await session.commit()
        await session.refresh(row)
        return store.serialize(row)


@router.get("/conversations/{conversation_id}/runs")
async def runs(conversation_id: uuid.UUID, user: User = Depends(current_user)):
    return await store.list_runs(str(conversation_id), user.id)


@router.post("/runs")
async def start_run(body: RunInput, user: User = Depends(current_user)):
    conversation_id, run_id = str(body.conversation_id), str(body.id)
    if not body.query.strip():
        raise HTTPException(422, "请输入问题")
    async with store.sessions()() as session:
        await store.owned(session, conversation_id, user.id)
        if await session.get(store.WorkspaceRun, run_id):
            raise HTTPException(409, "请勿重复提交同一执行")
    if conversation_id in active_conversations or run_id in active:
        raise HTTPException(409, "该会话正在执行，请等待完成")
    active_conversations.add(conversation_id)
    return StreamingResponse(execute_stream(run_id, conversation_id, body.query, user.id), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: uuid.UUID, user: User = Depends(current_user)):
    async with store.sessions()() as session:
        run = await session.get(store.WorkspaceRun, str(run_id))
        if run is None:
            raise HTTPException(404, "执行记录不存在")
        await store.owned(session, run.conversation_id, user.id)
    worker = active.get(str(run_id))
    if worker is not None and not worker.cancelling():
        worker.cancel()
    return {"ok": worker is not None}
