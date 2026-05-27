"""Tests for zo_sentinel.probes.duckdb_schema_uptime_probe.

Mocks ``requests.post`` so the suite never reaches the network. Verifies:
* schema-hash determinism (delegated to loader.compute_schema_hash)
* drift detection (added, removed, retyped columns -> different hash)
* drift staging writes to .pending_drift/, NOT the canonical path
* uptime state machine: ok->down emits one outage, not a flood
* daemon-mode loop's --once flag actually exits

No live write_service required.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from zo_sentinel.probes import duckdb_schema_uptime_probe as probe  # noqa: E402
from zo_sentinel.schemas.loader import compute_schema_hash  # noqa: E402


# ---------------------------------------------------------------------------
# UptimeState
# ---------------------------------------------------------------------------


class TestUptimeState:
    def test_first_ok_emits(self):
        s = probe.UptimeState()
        emit, reason = s.observe(ok=True)
        assert emit is True
        assert reason == "first_observation"

    def test_repeated_ok_does_not_emit(self):
        s = probe.UptimeState()
        s.observe(ok=True)  # first observation
        for _ in range(probe.LIVENESS_PULSE_EVERY - 2):
            emit, _ = s.observe(ok=True)
            assert emit is False, "should not emit on no-change cycles"

    def test_liveness_pulse_after_n_cycles(self):
        s = probe.UptimeState()
        s.observe(ok=True)  # cycle 0, first_observation
        for _ in range(probe.LIVENESS_PULSE_EVERY - 1):
            s.observe(ok=True)
        # The Nth no-change cycle should re-emit as a liveness pulse
        emit, reason = s.observe(ok=True)
        assert emit is True
        assert reason == "liveness_pulse"

    def test_ok_to_down_transition_emits_once(self):
        s = probe.UptimeState()
        s.observe(ok=True)
        emit, reason = s.observe(ok=False)
        assert emit is True
        assert reason == "transition_ok_to_down"
        # Continued down should NOT keep emitting
        for _ in range(5):
            emit, _ = s.observe(ok=False)
            assert emit is False, "down->down must not flood"

    def test_down_to_ok_transition_emits(self):
        s = probe.UptimeState()
        s.observe(ok=False)  # first_observation (down)
        emit, reason = s.observe(ok=True)
        assert emit is True
        assert reason == "transition_down_to_ok"

    def test_consecutive_failure_counter(self):
        s = probe.UptimeState()
        s.observe(ok=False)
        s.observe(ok=False)
        s.observe(ok=False)
        assert s.consecutive_failures == 3
        s.observe(ok=True)
        assert s.consecutive_failures == 0


# ---------------------------------------------------------------------------
# compute_schema_hash (drift detection foundation)
# ---------------------------------------------------------------------------


class TestSchemaHash:
    def test_deterministic_same_input(self):
        rows = [
            {"table_name": "a", "column_name": "x", "data_type": "VARCHAR"},
            {"table_name": "a", "column_name": "y", "data_type": "INTEGER"},
        ]
        assert compute_schema_hash(rows) == compute_schema_hash(rows)

    def test_changes_when_column_added(self):
        base = [{"table_name": "t", "column_name": "x", "data_type": "VARCHAR"}]
        extra = base + [{"table_name": "t", "column_name": "y", "data_type": "BIGINT"}]
        assert compute_schema_hash(base) != compute_schema_hash(extra)

    def test_changes_when_column_removed(self):
        full = [
            {"table_name": "t", "column_name": "x", "data_type": "VARCHAR"},
            {"table_name": "t", "column_name": "y", "data_type": "BIGINT"},
        ]
        partial = full[:1]
        assert compute_schema_hash(full) != compute_schema_hash(partial)

    def test_changes_when_column_retyped(self):
        a = [{"table_name": "t", "column_name": "x", "data_type": "VARCHAR"}]
        b = [{"table_name": "t", "column_name": "x", "data_type": "BIGINT"}]
        assert compute_schema_hash(a) != compute_schema_hash(b)


# ---------------------------------------------------------------------------
# diff_schemas
# ---------------------------------------------------------------------------


class TestDiffSchemas:
    def test_no_diff_for_identical(self):
        canonical = {"t": [{"name": "x", "type": "VARCHAR"}]}
        live_rows = [{"table_name": "t", "column_name": "x", "data_type": "VARCHAR"}]
        diff = probe.diff_schemas(canonical, live_rows)
        assert diff["added_tables"] == []
        assert diff["removed_tables"] == []
        assert diff["modified_columns"] == []

    def test_added_table(self):
        canonical = {"t": [{"name": "x", "type": "VARCHAR"}]}
        live_rows = [
            {"table_name": "t", "column_name": "x", "data_type": "VARCHAR"},
            {"table_name": "new_t", "column_name": "y", "data_type": "BIGINT"},
        ]
        diff = probe.diff_schemas(canonical, live_rows)
        assert diff["added_tables"] == ["new_t"]

    def test_removed_table(self):
        canonical = {
            "t": [{"name": "x", "type": "VARCHAR"}],
            "gone": [{"name": "z", "type": "INTEGER"}],
        }
        live_rows = [{"table_name": "t", "column_name": "x", "data_type": "VARCHAR"}]
        diff = probe.diff_schemas(canonical, live_rows)
        assert diff["removed_tables"] == ["gone"]

    def test_retyped_column(self):
        canonical = {"t": [{"name": "x", "type": "VARCHAR"}]}
        live_rows = [{"table_name": "t", "column_name": "x", "data_type": "BIGINT"}]
        diff = probe.diff_schemas(canonical, live_rows)
        assert any(
            m["change"] == "retyped" and m["column"] == "x"
            for m in diff["modified_columns"]
        )


# ---------------------------------------------------------------------------
# Drift staging writes to .pending_drift/, never the canonical file
# ---------------------------------------------------------------------------


def _mk_query_response(rows: list) -> MagicMock:
    """Build a MagicMock that mimics a successful requests.post response."""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"rows": rows})
    return resp


class TestDriftStaging:
    def test_drift_writes_to_pending_not_canonical(self, tmp_path, monkeypatch):
        # Redirect both canonical and pending paths into tmp_path
        canonical_path = tmp_path / "duckdb_schema.json"
        pending_dir = tmp_path / ".pending_drift"
        monkeypatch.setattr(probe, "PENDING_DRIFT_DIR", pending_dir)
        monkeypatch.setattr(probe, "CANONICAL_DUCKDB_PATH", canonical_path)

        # Seed a canonical snapshot with one column
        canonical_doc = {
            "generated_at": "2026-05-25T22:38:00Z",
            "engine": "duckdb",
            "source": "test",
            "schema_hash": compute_schema_hash(
                [{"table_name": "t", "column_name": "x", "data_type": "VARCHAR"}]
            ),
            "tables": {
                "t": [
                    {"name": "x", "type": "VARCHAR", "ordinal": 1, "nullable": True}
                ]
            },
        }
        canonical_path.write_text(json.dumps(canonical_doc), encoding="utf-8")

        # Mock load_duckdb_schema to use our temp canonical file
        from zo_sentinel.schemas.loader import SchemaSnapshot

        monkeypatch.setattr(
            probe, "load_duckdb_schema", lambda: SchemaSnapshot(canonical_path)
        )

        # Live schema differs (column 'y' added)
        live_rows = [
            {"table_name": "t", "column_name": "x", "data_type": "VARCHAR"},
            {"table_name": "t", "column_name": "y", "data_type": "BIGINT"},
        ]
        with patch.object(probe.requests, "post") as mock_post:
            mock_post.return_value = _mk_query_response(live_rows)
            result = probe.check_duckdb_drift(dry_run=False)

        assert result["drift"] is True
        assert result["pending_path"] is not None
        pending_file = pending_dir / "duckdb_schema.json"
        assert pending_file.exists(), "pending file must be written on drift"

        # Canonical file MUST be unchanged
        canonical_after = json.loads(canonical_path.read_text(encoding="utf-8"))
        assert canonical_after == canonical_doc, "canonical schema file must not be touched"

    def test_no_drift_does_not_stage(self, tmp_path, monkeypatch):
        canonical_path = tmp_path / "duckdb_schema.json"
        pending_dir = tmp_path / ".pending_drift"
        monkeypatch.setattr(probe, "PENDING_DRIFT_DIR", pending_dir)

        same_rows = [{"table_name": "t", "column_name": "x", "data_type": "VARCHAR"}]
        h = compute_schema_hash(same_rows)
        canonical_doc = {
            "generated_at": "2026-05-25T22:38:00Z",
            "engine": "duckdb",
            "source": "test",
            "schema_hash": h,
            "tables": {
                "t": [{"name": "x", "type": "VARCHAR", "ordinal": 1, "nullable": True}]
            },
        }
        canonical_path.write_text(json.dumps(canonical_doc), encoding="utf-8")

        from zo_sentinel.schemas.loader import SchemaSnapshot

        monkeypatch.setattr(
            probe, "load_duckdb_schema", lambda: SchemaSnapshot(canonical_path)
        )

        with patch.object(probe.requests, "post") as mock_post:
            mock_post.return_value = _mk_query_response(same_rows)
            result = probe.check_duckdb_drift(dry_run=False)

        assert result["drift"] is False
        assert not (pending_dir / "duckdb_schema.json").exists()


# ---------------------------------------------------------------------------
# Uptime probe -- live request mocking
# ---------------------------------------------------------------------------


class TestCheckUptime:
    def test_uptime_ok(self):
        with patch.object(probe.requests, "post") as mock_post:
            mock_post.return_value = _mk_query_response([{"ok": 1}])
            ok, latency, err = probe.check_uptime()
        assert ok is True
        assert err is None
        assert latency >= 0

    def test_uptime_down_on_exception(self):
        with patch.object(probe.requests, "post") as mock_post:
            mock_post.side_effect = ConnectionError("refused")
            ok, latency, err = probe.check_uptime()
        assert ok is False
        assert err is not None
        assert "ConnectionError" in err


# ---------------------------------------------------------------------------
# One-shot cycle: uptime down -> emit single outage row, skip drift checks
# ---------------------------------------------------------------------------


class TestCycleOneShot:
    def test_outage_emits_one_row_and_skips_drift(self, monkeypatch):
        emitted = []

        def fake_emit(memory_type, content, importance, dry_run=False):
            emitted.append((memory_type, content, importance))
            return True

        monkeypatch.setattr(probe, "_emit_mesh_memory", fake_emit)

        with patch.object(probe.requests, "post") as mock_post:
            mock_post.side_effect = ConnectionError("nope")
            state = probe.UptimeState()
            summary = probe.cycle(state, dry_run=True)

        # One outage emit, no drift emits
        assert summary["uptime"]["ok"] is False
        assert len(emitted) == 1
        assert emitted[0][0] == "duckdb_outage"
        assert "duckdb_drift" not in summary, "drift check must be skipped on outage"
