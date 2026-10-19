"""Two-pole test for the harness-durability hardening of weekly_rescore.py.

Covers the three defects that lost rows in a full-corpus v3.4 rescore:
  (1) non-idempotent import   -> ON CONFLICT ... DO UPDATE (POLE B)
  (2) collect marked done w/o preds landing -> post-condition + self-heal
  (3) destroy firing before results are durable -> _destroy durability gate (POLE A)

POLE A (gate, pure logic / monkeypatch, NO network):
  a stubbed _results_state returning '' makes the guarded (success-path) destroy
  REFUSE; returning 'ok' lets it proceed; the force=True (wedge/forensic) path
  destroys regardless of results state.

POLE B (idempotency, REAL pg against a THROWAWAY table):
  create _harden_test_axis with the SAME uq_axis_scores_natural (server_id,
  axis_name, model_version) constraint, insert a row with execute_values + the
  production ON CONFLICT clause, re-run the IDENTICAL insert, and assert no
  exception + a stable row count + an updated value. Never touches any prod
  table. Skips (not fails) if the fly PG proxy is unreachable in this env.
"""
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import weekly_rescore as wr  # noqa: E402


# --------------------------------------------------------------------------- #
# POLE A -- destroy durability gate (pure logic, monkeypatched, no network)
# --------------------------------------------------------------------------- #

class _FakeVast:
    """Records destroy calls; reports the instance gone so _destroy succeeds."""
    def __init__(self, *_a, **_k):
        self.destroyed = []

    def destroy_instance(self, id):  # noqa: A002 -- mirror SDK signature
        self.destroyed.append(id)

    def show_instances(self):
        return []  # nothing alive -> _destroy's post-check passes


def _fresh_run(tmp_path):
    r = wr.Run(tmp_path / "run")
    r.state = {
        "run_id": "TEST-RUN",
        "instance_id": "12345",
        "results_branch": "score-job-TEST-s0-results",
    }
    r.dir.mkdir(parents=True, exist_ok=True)
    return r


def _wire(monkeypatch, results_state, fake_vast):
    # vastai_sdk.VastAI is imported INSIDE _destroy; patch the attribute there.
    import types
    fake_mod = types.SimpleNamespace(VastAI=lambda *a, **k: fake_vast)
    monkeypatch.setitem(sys.modules, "vastai_sdk", fake_mod)
    monkeypatch.setattr(wr, "secret", lambda name: "x")
    monkeypatch.setattr(wr, "_results_state", lambda run, pat: results_state)
    monkeypatch.setattr(wr, "ledger", lambda *a, **k: None)
    monkeypatch.setattr(wr.time, "sleep", lambda *_a, **_k: None)


def test_pole_a_gate_refuses_when_results_absent(tmp_path, monkeypatch):
    """results_state '' (absent) -> success-path destroy REFUSES, pod survives."""
    fv = _FakeVast()
    _wire(monkeypatch, results_state="", fake_vast=fv)
    run = _fresh_run(tmp_path)
    with pytest.raises(SystemExit) as ei:
        wr._destroy(run, "ok")               # force defaults to False
    assert "refusing to destroy" in str(ei.value)
    assert fv.destroyed == []                # never called the vast API
    assert not run.state.get("destroyed")


def test_pole_a_gate_allows_when_results_ok(tmp_path, monkeypatch):
    """results_state 'ok' -> success-path destroy PROCEEDS."""
    fv = _FakeVast()
    _wire(monkeypatch, results_state="ok", fake_vast=fv)
    run = _fresh_run(tmp_path)
    wr._destroy(run, "ok")
    assert fv.destroyed == [12345]
    assert run.state.get("destroyed") is True


def test_pole_a_force_destroys_regardless(tmp_path, monkeypatch):
    """force=True (wedge/forensic no-data teardown) destroys even when results ''."""
    fv = _FakeVast()
    _wire(monkeypatch, results_state="", fake_vast=fv)   # worst case: no results
    run = _fresh_run(tmp_path)
    wr._destroy(run, "wedge", force=True)
    assert fv.destroyed == [12345]
    assert run.state.get("destroyed") is True


# --------------------------------------------------------------------------- #
# POLE B -- idempotent UPSERT against a THROWAWAY pg table (real PG)
# --------------------------------------------------------------------------- #

# The exact production write from ph_import._apply, parameterised on table name.
_UPSERT_SQL = (
    "insert into {tbl} (server_id, axis_name, label, label_index, probs, "
    "p_top, p_critical, p_danger, escalated, escalated_to, decision_rule_version, "
    "model_version, adapter_sha256, scored_at) values %s "
    "on conflict (server_id, axis_name, model_version) do update set "
    "label=excluded.label, label_index=excluded.label_index, probs=excluded.probs, "
    "p_top=excluded.p_top, p_critical=excluded.p_critical, p_danger=excluded.p_danger, "
    "escalated=excluded.escalated, escalated_to=excluded.escalated_to, "
    "decision_rule_version=excluded.decision_rule_version, "
    "adapter_sha256=excluded.adapter_sha256, scored_at=excluded.scored_at"
)

_TBL = "_harden_test_axis"


def _pg_or_skip():
    try:
        wr.ensure_proxy()
        return wr.pg_conn()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"fly PG proxy unreachable in this env ({e.__class__.__name__}: {e})")


def test_pole_b_upsert_is_conflict_safe():
    from datetime import datetime
    import json as _json
    from psycopg2.extras import execute_values

    conn = _pg_or_skip()
    cur = conn.cursor()
    try:
        # Throwaway table with the SAME natural unique constraint as prod.
        cur.execute(f"drop table if exists {_TBL}")
        cur.execute(
            f"create table {_TBL} ("
            " id bigserial primary key,"
            " server_id varchar(128), axis_name varchar(64), label varchar(64),"
            " label_index integer, probs jsonb, p_top double precision,"
            " p_critical double precision, p_danger double precision,"
            " escalated boolean, escalated_to varchar(32),"
            " decision_rule_version varchar(32), model_version varchar(64),"
            " adapter_sha256 varchar(80), scored_at timestamp,"
            " constraint uq_harden_natural unique (server_id, axis_name, model_version))")
        conn.commit()

        def row(label, label_index, p_top):
            return (
                "srv-A", "overall_risk", label, label_index,
                _json.dumps([0.1, 0.2, 0.3, 0.4]), p_top, 0.4, None,
                False, None, "gate_rule_v1", "vTEST", "deadbeef",
                datetime.utcnow(),
            )

        # First insert: a brand-new row.
        execute_values(cur, _UPSERT_SQL.format(tbl=_TBL), [row("STRONG", 3, 0.9)])
        conn.commit()
        cur.execute(f"select count(*) from {_TBL}")
        assert cur.fetchone()[0] == 1

        # Re-run the IDENTICAL natural key with CHANGED score columns. The old
        # plain-INSERT path would raise duplicate key here and abort the txn;
        # the UPSERT must update in place -- no exception, still ONE row.
        execute_values(cur, _UPSERT_SQL.format(tbl=_TBL), [row("MODERATE", 2, 0.55)])
        conn.commit()
        cur.execute(f"select count(*) from {_TBL}")
        assert cur.fetchone()[0] == 1, "UPSERT duplicated the row instead of updating"

        # And the update actually landed (DO UPDATE SET took effect).
        cur.execute(f"select label, label_index, p_top from {_TBL} "
                    "where server_id='srv-A' and axis_name='overall_risk' "
                    "and model_version='vTEST'")
        got = cur.fetchone()
        assert got == ("MODERATE", 2, 0.55), f"stale row, got {got}"

        # A third identical re-run is also a clean no-exception no-growth op.
        execute_values(cur, _UPSERT_SQL.format(tbl=_TBL), [row("MODERATE", 2, 0.55)])
        conn.commit()
        cur.execute(f"select count(*) from {_TBL}")
        assert cur.fetchone()[0] == 1
    finally:
        try:
            cur.execute(f"drop table if exists {_TBL}")
            conn.commit()
        except Exception:  # noqa: BLE001
            conn.rollback()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
