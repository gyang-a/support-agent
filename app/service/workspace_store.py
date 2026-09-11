"""Workspace presentation records stored alongside existing MySQL conversation data."""
from datetime import datetime, timezone
from sqlalchemy import JSON, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column
from fastapi import HTTPException
from app.service import chat_service
from core.persistence.models import Base

DEMO_USER = "user_1001"


class WorkspaceConversation(Base):
    __tablename__ = "workspace_conversations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))


class WorkspaceRun(Base):
    __tablename__ = "workspace_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    query: Mapped[str] = mapped_column(Text)
    response: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32))
    events: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String(40))


def now():
    return datetime.now(timezone.utc).isoformat()


def sessions():
    db = chat_service.database
    if db is None or not db.available or db.sessions is None:
        raise HTTPException(503, "会话存储未连接。请启动 MySQL 并检查 MYSQL_URL。")
    return db.sessions


def serialize(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns if column.name != "user_id"}


async def owned(session, conversation_id, account_id):
    row = await session.get(WorkspaceConversation, conversation_id)
    if row is None or row.user_id != account_id:
        raise HTTPException(404, "会话不存在")
    return row


async def save_run(run, account_id):
    async with sessions()() as session:
        conversation = await owned(session, run["conversation_id"], account_id)
        await session.merge(WorkspaceRun(**run))
        conversation.updated_at = now()
        await session.commit()


async def list_runs(conversation_id, account_id):
    async with sessions()() as session:
        await owned(session, conversation_id, account_id)
        rows = (await session.execute(select(WorkspaceRun).where(WorkspaceRun.conversation_id == conversation_id).order_by(WorkspaceRun.created_at))).scalars()
        return [serialize(row) for row in rows]


async def recover_interrupted_runs():
    """Single-worker recovery: a previous process cannot still own these runs."""
    db = chat_service.database
    if db is None or not db.available:
        return
    async with sessions()() as session:
        rows = (await session.execute(select(WorkspaceRun).where(WorkspaceRun.status == "running"))).scalars()
        for row in rows:
            row.status = "cancelled"
            events = list(row.events or [])
            events.append(dict(type="run.cancelled", sequence=len(events) + 1, timestamp=now(), data=dict(label="服务重启，本轮执行已中断")))
            row.events = events
        await session.commit()
