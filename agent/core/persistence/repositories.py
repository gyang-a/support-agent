"""会话消息与结构化用户偏好的 MySQL 仓储。"""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import (
    ConversationMessage,
    ConversationSession,
    PreferenceExtractionCursor,
    UserPreference,
)


def thread_id_for(user_id: str, session_id: str) -> str:
    # 固定长度避免 MySQL 复合主键超过 utf8mb4 索引上限，也不在 Checkpoint
    # 表名键中直接暴露用户标识。
    return hashlib.sha256(f"{user_id}\0{session_id}".encode("utf-8")).hexdigest()


def preference_key_for(content: str) -> str:
    """把常见偏好归入可覆盖的槽位，未知偏好使用稳定内容键去重。"""
    text = content.lower()
    if any(value in text for value in ("苹果", "apple", "华为", "小米", "荣耀", "oppo", "vivo")):
        return "phone.brand"
    if "预算" in text or re.search(r"\d+\s*元", text):
        return "digital.budget"
    if any(value in text for value in ("拍照", "游戏", "办公", "编程", "视频")):
        return "digital.usage"
    if any(value in text for value in ("大屏", "小屏", "尺寸", "轻薄")):
        return "device.form_factor"
    digest = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:20]
    return f"general.{digest}"


class ConversationRepository:
    def __init__(self, sessions: async_sessionmaker[Any]) -> None:
        self.sessions = sessions

    async def save_turn(
        self,
        user_id: str,
        session_id: str,
        user_content: str,
        assistant_content: str,
        *,
        agent_name: str | None = None,
        trace_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        thread_id = thread_id_for(user_id, session_id)
        request_id = request_id or uuid.uuid4().hex
        session_values = {
            "thread_id": thread_id,
            "user_id": user_id,
            "session_id": session_id,
            "status": "active",
        }
        session_stmt = mysql_insert(ConversationSession).values(**session_values)
        session_stmt = session_stmt.on_duplicate_key_update(
            status="active",
            updated_at=func.now(),
        )
        async with self.sessions() as session:
            await session.execute(session_stmt)
            session.add_all(
                [
                    ConversationMessage(
                        request_id=request_id,
                        thread_id=thread_id,
                        role="user",
                        content=user_content,
                        trace_id=trace_id,
                    ),
                    ConversationMessage(
                        request_id=request_id,
                        thread_id=thread_id,
                        role="assistant",
                        content=assistant_content,
                        agent_name=agent_name,
                        trace_id=trace_id,
                    ),
                ]
            )
            await session.commit()

    async def get_recent(self, user_id: str, session_id: str, limit: int = 10) -> list[dict[str, str]]:
        statement = (
            select(ConversationMessage)
            .where(ConversationMessage.thread_id == thread_id_for(user_id, session_id))
            .order_by(ConversationMessage.id.desc())
            .limit(max(1, min(limit, 100)))
        )
        async with self.sessions() as session:
            rows = (await session.execute(statement)).scalars().all()
        return [
            {"role": row.role, "content": row.content}
            for row in reversed(rows)
        ]


class PreferenceRepository:
    def __init__(self, sessions: async_sessionmaker[Any]) -> None:
        self.sessions = sessions

    async def list_preferences(self, user_id: str) -> list[str]:
        statement = (
            select(UserPreference)
            .where(UserPreference.user_id == user_id)
            .order_by(UserPreference.preference_key)
        )
        async with self.sessions() as session:
            rows = (await session.execute(statement)).scalars().all()
        return [row.preference_value for row in rows]

    async def upsert(self, user_id: str, content: str, source_text: str | None = None) -> None:
        values = {
            "user_id": user_id,
            "preference_key": preference_key_for(content),
            "preference_value": content[:2048],
            "source_text": (source_text or "")[:2048] or None,
            "confidence": "1.0",
        }
        statement = mysql_insert(UserPreference).values(**values)
        statement = statement.on_duplicate_key_update(
            preference_value=statement.inserted.preference_value,
            source_text=statement.inserted.source_text,
            confidence=statement.inserted.confidence,
            updated_at=func.now(),
        )
        async with self.sessions() as session:
            await session.execute(statement)
            await session.commit()

    async def list_users_with_pending_messages(self, limit: int = 100) -> list[str]:
        """列出存在尚未提取用户消息的用户。"""
        cursor_id = func.coalesce(PreferenceExtractionCursor.last_message_id, 0)
        statement = (
            select(ConversationSession.user_id)
            .join(
                ConversationMessage,
                ConversationMessage.thread_id == ConversationSession.thread_id,
            )
            .outerjoin(
                PreferenceExtractionCursor,
                PreferenceExtractionCursor.user_id == ConversationSession.user_id,
            )
            .where(
                ConversationMessage.role == "user",
                ConversationMessage.id > cursor_id,
            )
            .group_by(ConversationSession.user_id)
            .order_by(func.min(ConversationMessage.id))
            .limit(max(1, min(limit, 1000)))
        )
        async with self.sessions() as session:
            return list((await session.execute(statement)).scalars().all())

    async def get_pending_user_messages(
        self, user_id: str, limit: int = 200
    ) -> tuple[list[tuple[int, str]], int, int]:
        """读取一批用户消息，返回消息、起始游标和批次末尾 ID。"""
        cursor_statement = select(PreferenceExtractionCursor.last_message_id).where(
            PreferenceExtractionCursor.user_id == user_id
        )
        async with self.sessions() as session:
            async with session.begin():
                # 先确保游标行存在，后续覆盖可用条件 UPDATE 做并发保护。
                await session.execute(
                    mysql_insert(PreferenceExtractionCursor)
                    .values(user_id=user_id, last_message_id=0)
                    .prefix_with("IGNORE")
                )
                last_message_id = (
                    await session.execute(cursor_statement)
                ).scalar_one()
            statement = (
                select(ConversationMessage.id, ConversationMessage.content)
                .join(
                    ConversationSession,
                    ConversationSession.thread_id == ConversationMessage.thread_id,
                )
                .where(
                    ConversationSession.user_id == user_id,
                    ConversationMessage.role == "user",
                    ConversationMessage.id > last_message_id,
                )
                .order_by(ConversationMessage.id)
                .limit(max(1, min(limit, 1000)))
            )
            rows = list((await session.execute(statement)).all())
        messages = [(int(row.id), row.content) for row in rows]
        return messages, last_message_id, (
            messages[-1][0] if messages else last_message_id
        )

    async def replace_all_and_advance(
        self,
        user_id: str,
        items: list[str],
        expected_last_message_id: int,
        last_message_id: int,
        *,
        source_text: str | None = None,
    ) -> None:
        """在同一事务中整体覆盖偏好并推进处理游标。"""
        # 这是完整快照，不再依赖旧的语义槽位做更新/去重。
        keyed_items = {
            f"snapshot.{index:02d}": item for index, item in enumerate(items)
        }
        cursor_statement = (
            update(PreferenceExtractionCursor)
            .where(
                PreferenceExtractionCursor.user_id == user_id,
                PreferenceExtractionCursor.last_message_id == expected_last_message_id,
            )
            .values(last_message_id=last_message_id, updated_at=func.now())
        )
        async with self.sessions() as session:
            async with session.begin():
                await session.execute(
                    delete(UserPreference).where(UserPreference.user_id == user_id)
                )
                session.add_all(
                    [
                        UserPreference(
                            user_id=user_id,
                            preference_key=key,
                            preference_value=item,
                            source_text=(source_text or "")[:2048] or None,
                            confidence="1.0",
                        )
                        for key, item in keyed_items.items()
                    ]
                )
                cursor_result = await session.execute(cursor_statement)
                if cursor_result.rowcount != 1:
                    raise RuntimeError(
                        "Preference cursor changed during extraction; snapshot was not written"
                    )
