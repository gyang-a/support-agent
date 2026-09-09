"""MySQL 持久化表模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import LONGBLOB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    agent_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UserPreference(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    preference_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    preference_value: Mapped[str] = mapped_column(Text)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str] = mapped_column(String(16), default="1.0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class PreferenceExtractionCursor(Base):
    """每个用户已经成功纳入偏好快照的最后一条消息。"""

    __tablename__ = "preference_extraction_cursors"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_message_id: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class GraphCheckpoint(Base):
    __tablename__ = "graph_checkpoints"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(255), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checkpoint_type: Mapped[str] = mapped_column(String(32))
    checkpoint_blob: Mapped[bytes] = mapped_column(LONGBLOB)
    metadata_type: Mapped[str] = mapped_column(String(32))
    metadata_blob: Mapped[bytes] = mapped_column(LONGBLOB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_graph_checkpoint_latest", "thread_id", "checkpoint_ns", "checkpoint_id"),
    )


class GraphCheckpointWrite(Base):
    __tablename__ = "graph_checkpoint_writes"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(255), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    write_index: Mapped[int] = mapped_column(primary_key=True)
    task_path: Mapped[str] = mapped_column(String(1024), default="")
    channel: Mapped[str] = mapped_column(String(128))
    value_type: Mapped[str] = mapped_column(String(32))
    value_blob: Mapped[bytes] = mapped_column(LONGBLOB)


class Product(Base):
    """可检索的商品主数据；规格与检索标签和 SKU 一起持久化。"""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sku_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    brand: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(160))
    variant: Mapped[str] = mapped_column(String(160), default="")
    specs: Mapped[dict] = mapped_column(JSON)
    use_cases: Mapped[list] = mapped_column(JSON)
    keywords: Mapped[list] = mapped_column(JSON)
    search_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    source_name: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_product_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_products_category_brand_status", "category", "brand", "status"),
    )


class ProductOffer(Base):
    """SKU 在指定地区和渠道的价格、库存快照。"""

    __tablename__ = "product_offers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    region: Mapped[str] = mapped_column(String(16), default="CN")
    channel: Mapped[str] = mapped_column(String(64), default="official")
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    current_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    original_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inventory_status: Mapped[str] = mapped_column(String(32), default="未知")
    available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    source_name: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "product_id", "region", "channel", name="uq_product_offer_scope"
        ),
        Index("ix_product_offers_scope_price", "region", "channel", "current_price"),
    )
