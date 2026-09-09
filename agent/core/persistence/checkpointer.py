"""基于 SQLAlchemy 的 LangGraph MySQL CheckpointSaver。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from sqlalchemy import and_, delete, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import GraphCheckpoint, GraphCheckpointWrite


class SQLAlchemyMySQLSaver(BaseCheckpointSaver):
    """保存完整 Checkpoint blob，并用独立表保存 pending writes。"""

    def __init__(self, sessions: async_sessionmaker[Any]) -> None:
        super().__init__()
        self.sessions = sessions

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        namespace = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = get_checkpoint_id(config)
        conditions = [
            GraphCheckpoint.thread_id == thread_id,
            GraphCheckpoint.checkpoint_ns == namespace,
        ]
        if checkpoint_id:
            conditions.append(GraphCheckpoint.checkpoint_id == checkpoint_id)
        statement = select(GraphCheckpoint).where(and_(*conditions))
        if not checkpoint_id:
            statement = statement.order_by(GraphCheckpoint.checkpoint_id.desc()).limit(1)
        async with self.sessions() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
            if row is None:
                return None
            writes = (
                await session.execute(
                    select(GraphCheckpointWrite)
                    .where(
                        GraphCheckpointWrite.thread_id == row.thread_id,
                        GraphCheckpointWrite.checkpoint_ns == row.checkpoint_ns,
                        GraphCheckpointWrite.checkpoint_id == row.checkpoint_id,
                    )
                    .order_by(GraphCheckpointWrite.task_id, GraphCheckpointWrite.write_index)
                )
            ).scalars().all()
        return self._to_tuple(row, writes)

    def _to_tuple(self, row: GraphCheckpoint, writes: Sequence[GraphCheckpointWrite]) -> CheckpointTuple:
        config = {
            "configurable": {
                "thread_id": row.thread_id,
                "checkpoint_ns": row.checkpoint_ns,
                "checkpoint_id": row.checkpoint_id,
            }
        }
        parent = (
            {
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.parent_checkpoint_id,
                }
            }
            if row.parent_checkpoint_id
            else None
        )
        return CheckpointTuple(
            config=config,
            checkpoint=self.serde.loads_typed((row.checkpoint_type, row.checkpoint_blob)),
            metadata=self.serde.loads_typed((row.metadata_type, row.metadata_blob)),
            parent_config=parent,
            pending_writes=[
                (
                    item.task_id,
                    item.channel,
                    self.serde.loads_typed((item.value_type, item.value_blob)),
                )
                for item in writes
            ],
        )

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        conditions = []
        if config:
            configurable = config["configurable"]
            conditions.append(GraphCheckpoint.thread_id == str(configurable["thread_id"]))
            if "checkpoint_ns" in configurable:
                conditions.append(
                    GraphCheckpoint.checkpoint_ns == str(configurable["checkpoint_ns"])
                )
        if before and (before_id := get_checkpoint_id(before)):
            conditions.append(GraphCheckpoint.checkpoint_id < before_id)
        statement = select(GraphCheckpoint)
        if conditions:
            statement = statement.where(and_(*conditions))
        statement = statement.order_by(GraphCheckpoint.checkpoint_id.desc())
        async with self.sessions() as session:
            rows = (await session.execute(statement)).scalars().all()
            emitted = 0
            for row in rows:
                metadata = self.serde.loads_typed((row.metadata_type, row.metadata_blob))
                if filter and any(metadata.get(key) != value for key, value in filter.items()):
                    continue
                writes = (
                    await session.execute(
                        select(GraphCheckpointWrite).where(
                            GraphCheckpointWrite.thread_id == row.thread_id,
                            GraphCheckpointWrite.checkpoint_ns == row.checkpoint_ns,
                            GraphCheckpointWrite.checkpoint_id == row.checkpoint_id,
                        )
                    )
                ).scalars().all()
                yield self._to_tuple(row, writes)
                emitted += 1
                if limit is not None and emitted >= limit:
                    break

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, Any],
    ) -> RunnableConfig:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        namespace = str(configurable.get("checkpoint_ns", ""))
        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(checkpoint)
        metadata_type, metadata_blob = self.serde.dumps_typed(
            get_checkpoint_metadata(config, metadata)
        )
        values = {
            "thread_id": thread_id,
            "checkpoint_ns": namespace,
            "checkpoint_id": checkpoint["id"],
            "parent_checkpoint_id": configurable.get("checkpoint_id"),
            "checkpoint_type": checkpoint_type,
            "checkpoint_blob": checkpoint_blob,
            "metadata_type": metadata_type,
            "metadata_blob": metadata_blob,
        }
        statement = mysql_insert(GraphCheckpoint).values(**values)
        statement = statement.on_duplicate_key_update(**values)
        async with self.sessions() as session:
            await session.execute(statement)
            await session.commit()
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint["id"],
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        configurable = config["configurable"]
        rows = []
        for index, (channel, value) in enumerate(writes):
            value_type, value_blob = self.serde.dumps_typed(value)
            rows.append(
                {
                    "thread_id": str(configurable["thread_id"]),
                    "checkpoint_ns": str(configurable.get("checkpoint_ns", "")),
                    "checkpoint_id": str(configurable["checkpoint_id"]),
                    "task_id": task_id,
                    "write_index": WRITES_IDX_MAP.get(channel, index),
                    "task_path": task_path,
                    "channel": channel,
                    "value_type": value_type,
                    "value_blob": value_blob,
                }
            )
        if not rows:
            return
        statement = mysql_insert(GraphCheckpointWrite).values(rows)
        statement = statement.on_duplicate_key_update(
            task_path=statement.inserted.task_path,
            channel=statement.inserted.channel,
            value_type=statement.inserted.value_type,
            value_blob=statement.inserted.value_blob,
        )
        async with self.sessions() as session:
            await session.execute(statement)
            await session.commit()

    async def adelete_thread(self, thread_id: str) -> None:
        async with self.sessions() as session:
            await session.execute(
                delete(GraphCheckpointWrite).where(
                    GraphCheckpointWrite.thread_id == thread_id
                )
            )
            await session.execute(
                delete(GraphCheckpoint).where(GraphCheckpoint.thread_id == thread_id)
            )
            await session.commit()
