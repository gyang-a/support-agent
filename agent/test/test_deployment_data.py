"""Snapshot integrity and overwrite guards; no API or production DB access."""
from datetime import datetime
from decimal import Decimal

import pytest

from scripts.deployment_data import (
    checked_manifest, digest, read_json, restore_vectors, rows_digest, write_json,
)


def test_mysql_checkpoint_binary_dates_and_decimal_roundtrip(tmp_path):
    rows = [[b"\x00\xffcheckpoint", datetime(2026, 9, 11, 1, 2, 3, 123456), Decimal("199.90"), None]]
    path = tmp_path / "sql.json"
    write_json(path, {"rows": rows})
    assert read_json(path, sql=True)["rows"] == rows
    assert rows_digest(rows) == rows_digest(read_json(path, sql=True)["rows"])
    assert rows_digest(rows * 2) != rows_digest(rows)


def test_corrupted_snapshot_refused_before_restore(tmp_path):
    data = tmp_path / "rows.json"
    write_json(data, {"rows": [[1]]})
    item = {"file": data.name, "count": 1, "sha256": digest(data)}
    write_json(tmp_path / "manifest.json", {"version": 1, "mysql": [item], "milvus": [item]})
    checked_manifest(tmp_path)
    write_json(data, {"rows": [[2]]})
    with pytest.raises(RuntimeError, match="integrity"):
        checked_manifest(tmp_path)


def test_restore_refuses_to_overwrite_different_vectors(monkeypatch):
    from scripts import deployment_data
    class ExistingCollection:
        def has_collection(self, name):
            return True
    monkeypatch.setattr(deployment_data, "vector_rows", lambda *args: [{"id": 1, "embedding": [0.5]}])
    with pytest.raises(RuntimeError, match="overwrite"):
        restore_vectors(ExistingCollection(), "target", {
            "schema": {"fields": [{"name": "id", "is_primary": True}]},
            "rows": [{"id": 1, "embedding": [0.6]}],
        })


def test_resume_inserts_only_missing_ids_and_restores_auto_id_setting(monkeypatch):
    from scripts import deployment_data
    class PartialCollection:
        def __init__(self):
            self.rows = [{"id": 1, "embedding": [0.5]}]
            self.properties = []
        def has_collection(self, name):
            return True
        def alter_collection_properties(self, name, properties):
            self.properties.append(properties)
        def insert(self, name, rows):
            assert [row["id"] for row in rows] == [2]
            self.rows.extend(rows)
        def flush(self, *args, **kwargs):
            pass
    client = PartialCollection()
    monkeypatch.setattr(deployment_data, "vector_rows", lambda *args: list(client.rows))
    restore_vectors(client, "target", {
        "schema": {"auto_id": True, "fields": [{"name": "id", "is_primary": True}]},
        "rows": [{"id": 1, "embedding": [0.5]}, {"id": 2, "embedding": [0.6]}],
    })
    assert client.properties == [{"allow_insert_auto_id": "true"}, {"allow_insert_auto_id": "false"}]
