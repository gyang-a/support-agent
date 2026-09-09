"""商品查询使用的同步 SQLAlchemy 引擎。

MCP 的商品工具是同步函数，因此商品仓储使用 PyMySQL 同步驱动。会话、
checkpoint 等应用持久化仍继续使用 asyncmy 异步驱动，两者连接同一 MySQL。
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

from config.settings import get_settings


def _sync_database_url(url: str) -> str:
    if not url:
        raise RuntimeError("MYSQL_URL 未配置，商品目录无法查询。")
    parsed = make_url(url)
    if parsed.get_backend_name() == "mysql":
        parsed = parsed.set(drivername="mysql+pymysql")
    return parsed.render_as_string(hide_password=False)


@lru_cache(maxsize=4)
def _create_catalog_engine(url: str) -> Engine:
    return create_engine(
        _sync_database_url(url),
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def get_catalog_engine() -> Engine:
    return _create_catalog_engine(get_settings().mysql_url)
