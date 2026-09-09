"""Neo4j 兼容关系查询，连接失败时回退到审核过的本地快照。"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from .repository import check_compatibility


logger = logging.getLogger(__name__)
_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "compatibility_graph.json"
_driver = None
_ready = False
_lock = asyncio.Lock()


async def _get_driver():
    global _driver, _ready
    async with _lock:
        if _driver is None:
            from config import get_settings
            from neo4j import AsyncGraphDatabase

            settings = get_settings()
            _driver = AsyncGraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
                connection_timeout=2,
            )
        if not _ready:
            await _driver.verify_connectivity()
            graph = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
            groups = [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "selector_json": json.dumps(item["selector"], ensure_ascii=False),
                }
                for item in graph["groups"]
            ]
            await _driver.execute_query(
                "UNWIND $items AS item MERGE (g:ProductGroup {id:item.id}) SET g += item",
                items=groups,
                database_=settings.neo4j_database,
            )
            await _driver.execute_query(
                "UNWIND $items AS item MERGE (a:Accessory {id:item.id}) SET a += item",
                items=graph["accessories"],
                database_=settings.neo4j_database,
            )
            await _driver.execute_query(
                """
                UNWIND $items AS item
                MATCH (a:Accessory {id:item.source}), (g:ProductGroup {id:item.target})
                MERGE (a)-[r:COMPATIBILITY]->(g)
                SET r += item
                """,
                items=graph["relations"],
                database_=settings.neo4j_database,
            )
            _ready = True
        return _driver


async def check_compatibility_graph(source: str, target: str) -> dict[str, Any]:
    """以 Neo4j 为关系事实源，故障时使用同版本 JSON 快照保证可用性。"""
    try:
        from config import get_settings

        settings = get_settings()
        driver = await _get_driver()
        records, _, _ = await driver.execute_query(
            "MATCH (:Accessory)-[r:COMPATIBILITY]->(:ProductGroup) RETURN properties(r) AS relation",
            database_=settings.neo4j_database,
        )
        result = check_compatibility(
            source,
            target,
            relations=[dict(record["relation"]) for record in records],
        )
        result["graph_source"] = "neo4j"
        return result
    except Exception as exc:
        logger.warning("Neo4j compatibility query failed, using JSON snapshot: %s", exc)
        result = check_compatibility(source, target)
        result["graph_source"] = "json_fallback"
        return result
