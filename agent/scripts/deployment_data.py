"""Offline, lossless data snapshots for this demo (no embedding API calls).

Supports the project's InnoDB base tables and default-partition Milvus collections.
Stop application writers during export. Restore only into empty services; retries
may resume this same snapshot but never overwrite unrelated data.
"""
from __future__ import annotations

import argparse
import base64
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pymilvus import CollectionSchema, MilvusClient
from sqlalchemy import create_engine, inspect

RECEIPT = "_deployment_snapshot"


def packed(value):
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode()}
    if isinstance(value, (datetime, date, time)):
        return {"$" + type(value).__name__: value.isoformat()}
    if isinstance(value, timedelta):
        return {"$timedelta": value.total_seconds()}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if hasattr(value, "item"):
        return value.item()
    if type(value).__name__ == "RepeatedScalarContainer":
        return list(value)
    raise TypeError(type(value).__name__)


def unpacked(value):
    if len(value) == 1:
        key, data = next(iter(value.items()))
        conversions = {"$bytes": base64.b64decode, "$datetime": datetime.fromisoformat,
                       "$date": date.fromisoformat, "$time": time.fromisoformat,
                       "$decimal": Decimal, "$timedelta": lambda x: timedelta(seconds=x)}
        if key in conversions:
            return conversions[key](data)
    return value


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=packed, allow_nan=False)


def write_json(path, value):
    path.write_text(canonical(value), encoding="utf-8")


def read_json(path, *, sql=False):
    return json.loads(path.read_text(encoding="utf-8"), object_hook=unpacked if sql else None)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows_digest(rows):
    # Order-independent, preserving duplicates and exact stored values.
    return hashlib.sha256("\n".join(sorted(canonical(row) for row in rows)).encode()).hexdigest()


def quoted(name):
    return "`" + name.replace("`", "``") + "`"


def clients(mysql_url=None, milvus_database=None):
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    url = mysql_url or os.environ["MYSQL_URL"]
    engine = create_engine(url.replace("mysql+asyncmy:", "mysql+pymysql:"), pool_pre_ping=True)
    milvus = MilvusClient(uri=f"http://{os.getenv('MILVUS_HOST', 'localhost')}:{os.getenv('MILVUS_PORT', '19530')}",
                          db_name=milvus_database or os.getenv("MILVUS_DATABASE", "default"))
    return engine, milvus


def vector_rows(client, name, schema):
    fields = [f["name"] for f in schema["fields"] if not f.get("is_function_output")]
    if schema.get("enable_dynamic_field"):
        raise RuntimeError(f"Dynamic fields unsupported: {name}")
    client.load_collection(name, timeout=120)
    iterator = client.query_iterator(collection_name=name, filter="", output_fields=fields,
                                     batch_size=100, consistency_level="Strong")
    rows = []
    try:
        while batch := iterator.next():
            rows.extend(dict(row) for row in batch)
    finally:
        iterator.close()
    return rows


def export_snapshot(engine, milvus, folder):
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "mysql": [], "milvus": []}
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        conn.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        if inspect(conn).get_view_names() or conn.exec_driver_sql("SHOW TRIGGERS").fetchall():
            raise RuntimeError("Views/triggers require a native MySQL backup")
        for index, name in enumerate(inspect(conn).get_table_names()):
            if name == RECEIPT:
                continue
            ddl = conn.exec_driver_sql(f"SHOW CREATE TABLE {quoted(name)}").one()[1]
            if "ENGINE=InnoDB" not in ddl:
                raise RuntimeError(f"Nontransactional table: {name}")
            result = conn.exec_driver_sql(f"SELECT * FROM {quoted(name)}")
            columns = list(result.keys())
            rows = [list(row) for row in result]
            file = f"mysql-{index}.json"
            write_json(folder / file, {"ddl": ddl, "columns": columns, "rows": rows})
            manifest["mysql"].append({"name": name, "file": file, "count": len(rows), "sha256": digest(folder / file)})
            print(f"Export MySQL {name}: {len(rows)}", flush=True)
        conn.rollback()
    for index, name in enumerate(sorted(milvus.list_collections())):
        schema = milvus.describe_collection(name)
        if milvus.list_partitions(name) != ["_default"] or schema.get("aliases"):
            raise RuntimeError(f"Custom partitions/aliases unsupported: {name}")
        indexes = [milvus.describe_index(name, i) for i in milvus.list_indexes(name)]
        rows = vector_rows(milvus, name, schema)
        file = f"milvus-{index}.json"
        write_json(folder / file, {"schema": schema, "indexes": indexes, "rows": rows})
        manifest["milvus"].append({"name": name, "file": file, "count": len(rows), "sha256": digest(folder / file)})
        print(f"Export Milvus {name}: {len(rows)}", flush=True)
    write_json(folder / "manifest.json", manifest)  # Incomplete exports cannot be restored.
    print(f"Snapshot complete: {folder}", flush=True)


def checked_manifest(folder):
    manifest = read_json(folder / "manifest.json")
    if manifest["version"] != 1 or not manifest["mysql"] or not manifest["milvus"]:
        raise RuntimeError("Empty or unsupported snapshot")
    for item in manifest["mysql"] + manifest["milvus"]:
        path = folder / item["file"]
        if path.resolve().parent != folder.resolve() or digest(path) != item["sha256"]:
            raise RuntimeError("Snapshot file integrity failure")
        if len(read_json(path)["rows"]) != item["count"]:
            raise RuntimeError("Snapshot row count mismatch")
    return manifest


def create_vector_collection(client, name, data):
    schema = CollectionSchema.construct_from_dict(data["schema"])
    indexes = client.prepare_index_params()
    for index in data["indexes"]:
        params = {k: v for k, v in index.items() if k not in {
            "field_name", "index_name", "index_type", "metric_type", "total_rows",
            "indexed_rows", "pending_index_rows", "state", "fail_reason"}}
        indexes.add_index(field_name=index["field_name"], index_name=index["index_name"],
                          index_type=index["index_type"], metric_type=index.get("metric_type", ""), params=params)
    client.create_collection(collection_name=name, schema=schema, index_params=indexes,
                             consistency_level="Strong", num_shards=data["schema"].get("num_shards", 1))


def restore_vectors(client, name, data):
    if not client.has_collection(name):
        create_vector_collection(client, name, data)
    current = vector_rows(client, name, data["schema"])
    if rows_digest(current) == rows_digest(data["rows"]):
        print(f"Verified Milvus {name}: {len(current)}", flush=True)
        return
    # Permit retry only when every existing record is an exact member of the snapshot.
    primary = next(f["name"] for f in data["schema"]["fields"] if f.get("is_primary"))
    expected = {row[primary]: canonical(row) for row in data["rows"]}
    if len(expected) != len(data["rows"]) or any(expected.get(row[primary]) != canonical(row) for row in current):
        raise RuntimeError(f"Refusing to overwrite different Milvus data: {name}")
    existing = {row[primary] for row in current}
    missing = [row for row in data["rows"] if row[primary] not in existing]
    auto = data["schema"].get("auto_id", False)
    if auto:
        client.alter_collection_properties(name, {"allow_insert_auto_id": "true"})
    try:
        for start in range(0, len(missing), 100):
            client.insert(name, missing[start:start + 100])
        client.flush(name, timeout=120)
    finally:
        if auto:
            client.alter_collection_properties(name, {"allow_insert_auto_id": "false"})
    restored = vector_rows(client, name, data["schema"])
    if rows_digest(restored) != rows_digest(data["rows"]):
        raise RuntimeError(f"Milvus content verification failed: {name}")
    print(f"Restored and verified Milvus {name}: {len(restored)}", flush=True)


def restore_snapshot(engine, milvus, folder):
    manifest = checked_manifest(folder)
    snapshot_id = digest(folder / "manifest.json")
    with engine.begin() as conn:
        tables = inspect(conn).get_table_names()
        if RECEIPT in tables:
            receipt = conn.exec_driver_sql(f"SELECT snapshot_id, status FROM {RECEIPT} WHERE id=1").one()
            if receipt[0] != snapshot_id:
                raise RuntimeError("Destination belongs to a different snapshot; automatic overwrite refused")
            if receipt[1] == "ready":
                # Sessions expire and users can delete conversations. Only detect missing stores;
                # full content/count verification was performed before recording 'ready'.
                for item in manifest["mysql"]:
                    if item["name"] not in tables:
                        raise RuntimeError("Previously restored MySQL data is missing")
                for item in manifest["milvus"]:
                    if not milvus.has_collection(item["name"]):
                        raise RuntimeError("Previously restored Milvus collection is missing")
                print("Snapshot already restored; preserving subsequent application changes", flush=True)
                return
        else:
            if tables or milvus.list_collections():
                raise RuntimeError("First restore requires empty MySQL and Milvus databases")
            conn.exec_driver_sql(f"CREATE TABLE {RECEIPT} (id INT PRIMARY KEY, snapshot_id VARCHAR(64) NOT NULL, status VARCHAR(16) NOT NULL)")
            conn.exec_driver_sql(f"INSERT INTO {RECEIPT} VALUES (1,%s,'restoring')", (snapshot_id,))
    # MySQL DDL commits implicitly. Each table's rows are a separate atomic transaction.
    for item in manifest["mysql"]:
        data = read_json(folder / item["file"], sql=True)
        with engine.connect() as conn:
            conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
            try:
                if item["name"] not in inspect(conn).get_table_names():
                    conn.exec_driver_sql(data["ddl"])
                columns = ",".join(quoted(col) for col in data["columns"])
                current = [list(row) for row in conn.exec_driver_sql(f"SELECT {columns} FROM {quoted(item['name'])}")]
                if current and rows_digest(current) != rows_digest(data["rows"]):
                    raise RuntimeError(f"Refusing to overwrite different MySQL data: {item['name']}")
                if not current and data["rows"]:
                    statement = f"INSERT INTO {quoted(item['name'])} ({columns}) VALUES ({','.join(['%s'] * len(data['columns']))})"
                    for start in range(0, len(data["rows"]), 100):
                        conn.exec_driver_sql(statement, [tuple(row) for row in data["rows"][start:start + 100]])
                restored = [list(row) for row in conn.exec_driver_sql(f"SELECT {columns} FROM {quoted(item['name'])}")]
                if rows_digest(restored) != rows_digest(data["rows"]):
                    raise RuntimeError(f"MySQL content verification failed: {item['name']}")
                conn.commit()
            finally:
                conn.rollback()
                conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")
                conn.commit()
        print(f"Restored and verified MySQL {item['name']}: {len(restored)}", flush=True)
    for item in manifest["milvus"]:
        restore_vectors(milvus, item["name"], read_json(folder / item["file"]))
    with engine.begin() as conn:
        conn.exec_driver_sql(f"UPDATE {RECEIPT} SET status='ready' WHERE id=1")
    print("MySQL + Milvus restore and full content verification complete", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["export", "restore", "clone-vectors"])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--source", default="digital_technical_knowledge_v3_1024")
    parser.add_argument("--target", default="digital_technical_knowledge_v3_1024_sf_qwen3_06b_v1")
    args = parser.parse_args()
    engine, milvus = clients()
    try:
        if args.command == "export":
            export_snapshot(engine, milvus, args.folder)
        elif args.command == "restore":
            restore_snapshot(engine, milvus, args.folder)
        else:
            manifest = checked_manifest(args.folder)
            item = next(i for i in manifest["milvus"] if i["name"] == args.source)
            if args.source == args.target:
                raise ValueError("Source and target must differ")
            restore_vectors(milvus, args.target, read_json(args.folder / item["file"]))
    finally:
        engine.dispose()
        milvus.close()


if __name__ == "__main__":
    main()
