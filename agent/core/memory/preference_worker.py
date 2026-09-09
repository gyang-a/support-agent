"""周期性地把未处理对话折叠为用户的完整偏好快照。"""

from __future__ import annotations

import asyncio
import logging
import re

from core.persistence import PreferenceRepository

from .preference_extractor import PreferenceExtractor


logger = logging.getLogger(__name__)
_CLEAR_ALL = re.compile(r"(?:忘掉|删除|清除|清空).{0,8}(?:全部|所有).{0,8}(?:偏好|记忆)")


class PreferenceExtractionWorker:
    def __init__(
        self,
        repository: PreferenceRepository,
        extractor: PreferenceExtractor,
        *,
        user_limit: int = 100,
        message_limit: int = 200,
    ) -> None:
        self.repository = repository
        self.extractor = extractor
        self.user_limit = user_limit
        self.message_limit = message_limit
        self._user_locks: dict[str, asyncio.Lock] = {}

    async def run_once(self) -> int:
        """处理一轮待提取用户，返回成功推进游标的用户数量。"""
        processed = 0
        user_ids = await self.repository.list_users_with_pending_messages(self.user_limit)
        for user_id in user_ids:
            try:
                if await self.process_user(user_id):
                    processed += 1
            except Exception:
                logger.exception("Background preference extraction failed for user %s", user_id)
        return processed

    async def process_user(self, user_id: str) -> bool:
        """串行处理单个用户，避免后台轮询与显式触发相互覆盖。"""
        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            return await self._process_user(user_id)

    async def _process_user(self, user_id: str) -> bool:
        messages, cursor_before, batch_end_id = await self.repository.get_pending_user_messages(
            user_id, self.message_limit
        )
        if not messages:
            return False

        existing = await self.repository.list_preferences(user_id)
        conversation_text = "\n".join(
            f"[message_id={message_id}] user: {content.strip()}"
            for message_id, content in messages
            if content.strip()
        )
        if not conversation_text:
            return False

        full_items = await self.extractor.extract_full(conversation_text, existing)
        if full_items is None:
            return False
        if existing and not full_items and not _CLEAR_ALL.search(conversation_text):
            logger.warning(
                "Rejected empty preference snapshot for user %s without clear-all request",
                user_id,
            )
            return False

        await self.repository.replace_all_and_advance(
            user_id,
            full_items,
            cursor_before,
            batch_end_id,
        )
        return True
