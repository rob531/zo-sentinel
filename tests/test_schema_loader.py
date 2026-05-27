"""Tests for zo_sentinel.schemas.loader.

Cross-platform; runs on Windows, Linux, and GH runners. No DuckDB / SQLite
required -- the loader just reads committed JSON.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Make the repo root importable when pytest is invoked from anywhere.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from zo_sentinel.schemas.loader import (  # noqa: E402
    ColumnSpec,
    SchemaSnapshot,
    StaleSchemaError,
    compute_schema_hash,
    load_duckdb_schema,
    load_mesh_memory_schema,
)


# ---------------------------------------------------------------------------
# Fixtures: synthetic snapshots so we don't depend on whatever's currently
# committed under schemas/ for behavioral tests.
# ---------------------------------------------------------------------------


def _write_snapshot(
    path: Path,
    *,
    engine: str = "duckdb",
    generated_at: str = "2026-05-25T22:38:00Z",
    tables: dict | None = None,
    schema_hash: str = "deadbeef",
    source: str = "test fixture",
) -> Path:
    doc = {
        "generated_at": generated_at,
        "engine": engine,
        "source": source,
        "schema_hash": schema_hash,
        "tables": tables
        if tables is not None
        else {
            "audit_log": [
                {"name": "event_id", "type": "VARCHAR", "ordinal": 1, "nullable": True},
                {"name": "timestamp", "type": "TIMESTAMPTZ", "ordinal": 2, "nullable": False},
            ],
            "mcp_decisions": [
                {"name": "id", "type": "BIGINT", "ordinal": 1, "nullable": False},
                {"name": "decision", "type": "VARCHAR", "ordinal": 2, "nullable": True},
            ],
        },
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def fresh_snapshot(tmp_path: Path) -> SchemaSnapshot:
    p = _write_snapshot(
        tmp_path / "duckdb_schema.json",
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    return SchemaSnapshot(p)


@pytest.fixture
def stale_snapshot(tmp_path: Path) -> SchemaSnapshot:
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    p = _write_snapshot(tmp_path / "duckdb_schema.json", generated_at=old)
    return SchemaSnapshot(p)


# ---------------------------------------------------------------------------
# Behavioural tests against synthetic snapshots
# ---------------------------------------------------------------------------


class TestSchemaSnapshot:
    def test_loads_json(self, fresh_snapshot: SchemaSnapshot):
        assert fresh_snapshot.engine == "duckdb"
        assert "audit_log" in fresh_snapshot.tables()

    def test_tables_sorted(self, fresh_snapshot: SchemaSnapshot):
        names = fresh_snapshot.tables()
        assert names == sorted(names)

    def test_columns_returns_columnspec(self, fresh_snapshot: SchemaSnapshot):
        cols = fresh_snapshot.columns("audit_log")
        assert all(isinstance(c, ColumnSpec) for c in cols)
        assert cols[0].name == "event_id"
        assert cols[0].type == "VARCHAR"

    def test_column_names_in_ordinal_order(self, tmp_path: Path):
        # Deliberately list columns in reverse-ordinal order in the file;
        # column_names() should return them in ordinal order.
        tables = {
            "t": [
                {"name": "c_third", "type": "VARCHAR", "ordinal": 3, "nullable": True},
                {"name": "c_first", "type": "VARCHAR", "ordinal": 1, "nullable": True},
                {"name": "c_second", "type": "VARCHAR", "ordinal": 2, "nullable": True},
            ]
        }
        p = _write_snapshot(tmp_path / "s.json", tables=tables)
        snap = SchemaSnapshot(p)
        assert snap.column_names("t") == ["c_first", "c_second", "c_third"]

    def test_unknown_table_raises(self, fresh_snapshot: SchemaSnapshot):
        with pytest.raises(KeyError):
            fresh_snapshot.columns("does_not_exist")

    def test_has_table(self, fresh_snapshot: SchemaSnapshot):
        assert fresh_snapshot.has_table("audit_log")
        assert not fresh_snapshot.has_table("nope")

    def test_generated_at_parsing_z_suffix(self, tmp_path: Path):
        p = _write_snapshot(tmp_path / "s.json", generated_at="2026-05-25T22:38:00Z")
        snap = SchemaSnapshot(p)
        assert snap.generated_at.tzinfo is not None
        assert snap.generated_at.year == 2026

    def test_is_stale_false_for_recent(self, fresh_snapshot: SchemaSnapshot):
        assert fresh_snapshot.is_stale is False

    def test_is_stale_true_for_old(self, stale_snapshot: SchemaSnapshot):
        assert stale_snapshot.is_stale is True

    def test_assert_fresh_raises_when_stale(self, stale_snapshot: SchemaSnapshot):
        with pytest.raises(StaleSchemaError):
            stale_snapshot.assert_fresh()

    def test_assert_fresh_silent_when_fresh(self, fresh_snapshot: SchemaSnapshot):
        # Should not raise
        fresh_snapshot.assert_fresh()


class TestValidateRow:
    def test_valid_row_returns_empty_list(self, fresh_snapshot: SchemaSnapshot):
        # audit_log: event_id (nullable), timestamp (NOT nullable)
        row = {"event_id": "x", "timestamp": "2026-01-01T00:00:00Z"}
        assert fresh_snapshot.validate_row("audit_log", row) == []

    def test_unknown_column_flagged(self, fresh_snapshot: SchemaSnapshot):
        row = {"event_id": "x", "timestamp": "2026-01-01T00:00:00Z", "bogus": 1}
        errs = fresh_snapshot.validate_row("audit_log", row)
        assert any("unknown column" in e and "bogus" in e for e in errs)

    def test_missing_required_column_flagged(self, fresh_snapshot: SchemaSnapshot):
        # 'timestamp' is non-nullable in the fixture
        row = {"event_id": "x"}
        errs = fresh_snapshot.validate_row("audit_log", row)
        assert any("missing required" in e and "timestamp" in e for e in errs)

    def test_unknown_table_flagged(self, fresh_snapshot: SchemaSnapshot):
        errs = fresh_snapshot.validate_row("nope", {})
        assert errs and "unknown table" in errs[0]


class TestErrorPaths:
    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            SchemaSnapshot(tmp_path / "missing.json")

    def test_invalid_json_raises(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("not json at all {", encoding="utf-8")
        with pytest.raises(ValueError):
            SchemaSnapshot(p)

    def test_missing_tables_key_raises(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"engine": "duckdb"}), encoding="utf-8")
        with pytest.raises(ValueError):
            SchemaSnapshot(p)

    def test_missing_generated_at_raises_on_access(self, tmp_path: Path):
        p = tmp_path / "s.json"
        doc = {"engine": "duckdb", "tables": {}, "schema_hash": ""}
        p.write_text(json.dumps(doc), encoding="utf-8")
        snap = SchemaSnapshot(p)
        with pytest.raises(ValueError):
            _ = snap.generated_at


class TestComputeSchemaHash:
    def test_deterministic(self):
        rows = [
            {"table_name": "a", "column_name": "x", "data_type": "VARCHAR"},
            {"table_name": "a", "column_name": "y", "data_type": "INTEGER"},
        ]
        assert compute_schema_hash(rows) == compute_schema_hash(rows)

    def test_order_independent(self):
        a = [
            {"table_name": "t", "column_name": "x", "data_type": "VARCHAR"},
            {"table_name": "t", "column_name": "y", "data_type": "INTEGER"},
        ]
        b = list(reversed(a))
        assert compute_schema_hash(a) == compute_schema_hash(b)

    def test_changes_on_added_column(self):
        a = [{"table_name": "t", "column_name": "x", "data_type": "VARCHAR"}]
        b = a + [{"table_name": "t", "column_name": "y", "data_type": "INTEGER"}]
        assert compute_schema_hash(a) != compute_schema_hash(b)

    def test_changes_on_retyped_column(self):
        a = [{"table_name": "t", "column_name": "x", "data_type": "VARCHAR"}]
        b = [{"table_name": "t", "column_name": "x", "data_type": "BIGINT"}]
        assert compute_schema_hash(a) != compute_schema_hash(b)


# ---------------------------------------------------------------------------
# One integration test against the actually-committed snapshots so we catch
# corruption / accidental schema-file edits.
# ---------------------------------------------------------------------------


class TestCommittedSnapshots:
    def test_load_duckdb_schema_real_file(self):
        snap = load_duckdb_schema()
        assert snap.engine == "duckdb"
        assert len(snap.tables()) > 0
        # Sanity: audit_log is a load-bearing table for the Phase 0b probes.
        assert "audit_log" in snap.tables()
        assert snap.schema_hash, "schema_hash must be present in committed snapshot"

    def test_load_mesh_memory_schema_real_file(self):
        snap = load_mesh_memory_schema()
        assert snap.engine == "sqlite"
        assert "mesh_memory" in snap.tables()
        # The 5 canonical columns documented in gh_actions_fetcher.py
        names = snap.column_names("mesh_memory")
        for required in ("agent_id", "memory_type", "content", "importance", "created_at"):
            assert required in names, f"mesh_memory missing {required}"
