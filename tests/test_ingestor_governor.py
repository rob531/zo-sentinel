"""Hermetic tests for the auto-activation governor (zo_sentinel.ingestor.governor).

No host, no gate_errors.db: gate_8 verdicts come from InMemoryGate8Source, mesh
state from InMemoryMeshStore. Proves the readiness bar (self-smoke + gate_8
agreement + N green cycles / K agreeing artifacts), fully-automatic latch
creation, the veto freeze, and the CI-safe default (no oracle -> never activates).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from zo_sentinel.ingestor.ingestor import ArtifactIngestor, SENTINEL_NAME  # noqa: E402
from zo_sentinel.ingestor.store import InMemoryMeshStore  # noqa: E402
from zo_sentinel.ingestor.governor import (  # noqa: E402
    ActivationCriteria,
    AutoActivationGovernor,
    DuckDBGate8Source,
    InMemoryGate8Source,
)

GOOD_PY = "VALUE = 1\ndef helper():\n    return VALUE\n"
BAD_ENRICH = "def compute_score(s):\n    return 'nope', {}\n"


def _crit():
    # small bar so tests converge fast
    return ActivationCriteria(min_consecutive_green=2, min_agreeing_artifacts=3,
                              min_agreement=0.9)


def _setup(tmp_path: Path, n_good: int = 3):
    """Write n_good good .py artifacts + seed store rows + a passing gate_8."""
    store = InMemoryMeshStore()
    gate8 = InMemoryGate8Source()
    artifacts = []
    for i in range(n_good):
        name = f"mod_{i}.py"
        (tmp_path / name).write_text(GOOD_PY, encoding="utf-8")
        artifacts.append((f"row_{i}", {"file": name, "built_at": f"2026-05-29T0{i}:00:00+00:00"}))
        gate8.set(name, True)            # gate_8 agrees: pass
    store._artifacts = artifacts          # noqa: SLF001 (test seam)
    ingestor = ArtifactIngestor(store, sentinel_home=str(tmp_path))  # dormant
    gov = AutoActivationGovernor(ingestor, gate8=gate8, store=store, criteria=_crit())
    return gov, store, gate8, ingestor


# --- self-smoke -------------------------------------------------------------

def test_self_smoke_passes(tmp_path: Path):
    gov, *_ = _setup(tmp_path)
    ok, detail = gov.self_smoke()
    assert ok, detail


# --- gate_8 source ----------------------------------------------------------

def test_gate8_source_matches_by_basename():
    g = InMemoryGate8Source({"a.py": True, "b.py": False})
    assert g.verdict_for("/abs/path/a.py") is True
    assert g.verdict_for("b.py") is False
    assert g.verdict_for("unknown.py") is None


def test_duckdb_gate8_uses_latest_run(tmp_path: Path):
    """A stale early-cohort fail must NOT outweigh a later passing rebuild --
    verdict_for keys off the most-recent run only (the sticky-fail fix)."""
    import pytest
    duckdb = pytest.importorskip("duckdb")
    db = str(tmp_path / "gate_errors.db")
    con = duckdb.connect(db)
    con.execute(
        "CREATE TABLE gate_checks (check_id VARCHAR, run_id VARCHAR, "
        "gate_name VARCHAR, check_name VARCHAR, status VARCHAR, "
        "duration_ms INTEGER, started_at TIMESTAMPTZ, details TEXT)"
    )

    def ins(cid, run, status, when):
        con.execute(
            "INSERT INTO gate_checks VALUES (?,?,?,?,?,?,?,?)",
            [cid, run, "gate_8_new_module",
             f"gate_8: foo.py static_safety [{run}]", status, 1, when, None])

    ins("c1", "run_old", "fail", "2026-01-01T00:00:00+00:00")   # ancient fail
    ins("c2", "run_new", "pass", "2026-06-01T00:00:00+00:00")   # recent pass
    con.close()
    # latest run passed -> True, despite the old fail in history
    assert DuckDBGate8Source(db_path=db).verdict_for("foo.py") is True

    con = duckdb.connect(db)
    con.execute(
        "INSERT INTO gate_checks VALUES (?,?,?,?,?,?,?,?)",
        ["c3", "run_newest", "gate_8_new_module",
         "gate_8: foo.py import [run_newest]", "fail", 1,
         "2026-06-02T00:00:00+00:00", None])
    con.close()
    # newest run failed -> False
    assert DuckDBGate8Source(db_path=db).verdict_for("foo.py") is False
    # never-evaluated file -> None
    assert DuckDBGate8Source(db_path=db).verdict_for("never.py") is None


# --- assessment -------------------------------------------------------------

class TestAssess:
    def test_green_when_agreeing(self, tmp_path: Path):
        gov, store, *_ = _setup(tmp_path)
        a = gov.assess_cycle(gov.load_state())
        assert a.self_smoke_ok and a.green
        assert a.comparable == 3 and a.agreed == 3 and a.false_promotes == 0
        assert len(a.new_agreeing) == 3

    def test_false_promote_blocks_green(self, tmp_path: Path):
        gov, store, gate8, _ = _setup(tmp_path)
        gate8.set("mod_0.py", False)   # gate_8 fails one the ingestor promotes
        a = gov.assess_cycle(gov.load_state())
        assert a.false_promotes == 1 and a.green is False

    def test_low_agreement_blocks_green(self, tmp_path: Path):
        # ingestor REJECTS a bad enrichment, but gate_8 says pass -> disagreement
        # (not a false-promote, since the ingestor didn't promote it)
        store = InMemoryMeshStore()
        gate8 = InMemoryGate8Source()
        (tmp_path / "x_enrichment.py").write_text(BAD_ENRICH, encoding="utf-8")
        store._artifacts = [("r", {"file": "x_enrichment.py", "built_at": "t"})]
        gate8.set("x_enrichment.py", True)
        gov = AutoActivationGovernor(
            ArtifactIngestor(store, sentinel_home=str(tmp_path)),
            gate8=gate8, store=store, criteria=_crit())
        a = gov.assess_cycle(gov.load_state())
        assert a.comparable == 1 and a.agreed == 0 and a.false_promotes == 0
        assert a.green is False


# --- activation flow --------------------------------------------------------

class TestActivation:
    def test_activates_after_N_green_cycles(self, tmp_path: Path):
        gov, store, *_ = _setup(tmp_path)
        r1 = gov.run_once()
        assert r1["action"] == "hold" and r1["consecutive_green"] == 1
        assert not (tmp_path / SENTINEL_NAME).exists()
        r2 = gov.run_once()
        assert r2["action"] == "activated" and r2["activated"] is True
        # content-bearing latch written with provenance label
        latch = tmp_path / SENTINEL_NAME
        assert latch.exists()
        label = json.loads(latch.read_text(encoding="utf-8"))
        assert label["enabled_by"] == "zo_sentinel.activation_governor"
        assert label["mode"] == "auto"
        # audit row emitted
        assert any(t == "audit_log" and r.get("event_type") == "INGESTOR_AUTO_ACTIVATED"
                   for (t, r) in store.writes)

    def test_activated_ingestor_is_enabled(self, tmp_path: Path):
        gov, store, _, ingestor = _setup(tmp_path)
        gov.run_once(); gov.run_once()
        assert ingestor.is_enabled() is True   # the latch the ingestor reads

    def test_never_activates_without_gate8_oracle(self, tmp_path: Path):
        # gate_8 unknown for everything (the CI situation) -> no agreeing artifacts
        gov, store, gate8, _ = _setup(tmp_path)
        gov.gate8 = InMemoryGate8Source()   # empty oracle
        for _ in range(5):
            gov.run_once()
        assert not (tmp_path / SENTINEL_NAME).exists()
        assert gov.load_state().activated is False

    def test_false_promote_prevents_activation(self, tmp_path: Path):
        gov, store, gate8, _ = _setup(tmp_path)
        gate8.set("mod_0.py", False)
        for _ in range(5):
            gov.run_once()
        assert gov.load_state().activated is False


# --- veto -------------------------------------------------------------------

class TestVeto:
    def test_veto_blocks_activation(self, tmp_path: Path):
        gov, store, *_ = _setup(tmp_path)
        (tmp_path / ".no_auto_activate").write_text("", encoding="utf-8")
        for _ in range(5):
            r = gov.run_once()
            assert r["action"] == "vetoed"
        assert not (tmp_path / SENTINEL_NAME).exists()

    def test_veto_freezes_active_ingestor(self, tmp_path: Path):
        gov, store, _, ingestor = _setup(tmp_path)
        gov.run_once(); gov.run_once()
        assert (tmp_path / SENTINEL_NAME).exists()
        # now veto -> next cycle removes the latch (freeze)
        (tmp_path / ".no_auto_activate").write_text("", encoding="utf-8")
        r = gov.run_once()
        assert r["action"] == "vetoed" and r["froze"] is True
        assert not (tmp_path / SENTINEL_NAME).exists()
        assert ingestor.is_enabled() is False


# --- restart resilience -----------------------------------------------------

def test_reasserts_latch_after_restart_wipe(tmp_path: Path):
    gov, store, *_ = _setup(tmp_path)
    gov.run_once(); gov.run_once()
    assert (tmp_path / SENTINEL_NAME).exists()
    # simulate a container restart wiping the local latch file (state persists)
    (tmp_path / SENTINEL_NAME).unlink()
    r = gov.run_once()
    assert r["action"] == "reasserted"
    assert (tmp_path / SENTINEL_NAME).exists()


# --- propose-only mode ------------------------------------------------------

def test_propose_only_does_not_write_latch(tmp_path: Path):
    gov, store, gate8, _ = _setup(tmp_path)
    gov.auto = False
    gov.run_once()
    r2 = gov.run_once()
    assert r2["action"] == "proposed"
    assert not (tmp_path / SENTINEL_NAME).exists()
    assert any(r.get("event_type") == "INGESTOR_ACTIVATION_READY"
               for (_t, r) in store.writes)
