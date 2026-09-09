"""将本地兼容性图谱同步到 Neo4j 的可选脚本。

本地 JSON 查询不依赖 Neo4j，便于开发和自动化测试。生产环境需要图查询、
可视化或规则治理时，可显式调用 ``ingest_compatibility_graph`` 完成同步。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase


_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "compatibility_graph.json"


def ingest_compatibility_graph(uri: str, username: str, password: str) -> dict[str, Any]:
    """幂等写入商品组、配件和兼容关系，返回写入规模。"""
    with _DATA_FILE.open("r", encoding="utf-8") as file:
        graph = json.load(file)
    groups = [
        {
            "id": item["id"],
            "name": item["name"],
            # Neo4j 属性不能直接保存嵌套 Map，序列化后仍可完整追溯规则。
            "selector_json": json.dumps(item["selector"], ensure_ascii=False),
        }
        for item in graph["groups"]
    ]

    with GraphDatabase.driver(uri, auth=(username, password)) as driver:
        driver.execute_query(
            "UNWIND $groups AS item MERGE (g:ProductGroup {id:item.id}) SET g += item",
            groups=groups,
            database_="neo4j",
        )
        driver.execute_query(
            "UNWIND $accessories AS item MERGE (a:Accessory {id:item.id}) SET a += item",
            accessories=graph["accessories"],
            database_="neo4j",
        )
        # 动态关系类型不直接拼接到 Cypher，统一使用 COMPATIBILITY 并把状态
        # 保存在属性上，避免配置数据成为查询注入入口。
        driver.execute_query(
            """
            UNWIND $relations AS item
            MATCH (a:Accessory {id:item.source}), (g:ProductGroup {id:item.target})
            MERGE (a)-[r:COMPATIBILITY]->(g)
            SET r += item
            """,
            relations=graph["relations"],
            database_="neo4j",
        )

    return {
        "groups": len(graph["groups"]),
        "accessories": len(graph["accessories"]),
        "relations": len(graph["relations"]),
    }
