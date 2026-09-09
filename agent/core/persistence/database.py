"""SQLAlchemy 异步 MySQL 生命周期。"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from .models import Base


logger = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        self.echo = echo
        self.engine: AsyncEngine | None = None
        self.sessions: async_sessionmaker[Any] | None = None
        self.available = False

    async def initialize(self) -> None:
        if not self.url:
            logger.warning("MySQL disabled: MYSQL_URL is empty")
            return
        try:
            self.engine = create_async_engine(
                self.url,
                echo=self.echo,
                pool_pre_ping=True,
                pool_recycle=1800,
            )
            async with self.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
            self.available = True
            logger.info("MySQL persistence initialized")
        except Exception as exc:
            logger.warning("MySQL persistence unavailable: %s", exc)
            if self.engine is not None:
                await self.engine.dispose()
            self.engine = None
            self.sessions = None
            self.available = False

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()
        self.available = False
