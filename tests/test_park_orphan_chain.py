"""test_park_orphan_chain.py -- the orphan-chain seam-audit that un-jams goose.

Locks the rule that keeps pending/ flowing (the 2026-10-29 ~15h stall):
  * ORPHAN -> PARK: a pending directive requiring a file that is absent on disk AND
    produced by NO other pending directive (its chain root was never generated).
  * WAITING -> KEEP: a pending directive whose missing requirement HAS a producer in
    pending/ -- normal chain ordering, not a jam.
  * SATISFIED -> KEEP: requirement already on disk.
  * PARKED -> REVIVE: a parked directive whose requirements are now satisfiable.

Hermetic: builds a fake directives tree under tmp_path; no network, no live queue.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "tools" / "park_orphan_chain_directives.py"
_spec = importlib.util.spec_from_file_location("park_orphan_chain_directives", _SRC)
poc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(poc)


def _mk(d: Path, name: str, *, requires=None, output_file=None):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps({
        "directive_id": name[:-5],
        "requires": requires or [],
        "output_file": output_file,
    }), encoding="utf-8")


def _setup(tmp_path):
    repo = tmp_path
    pend = repo / "directives" / "pending"
    parked = repo / "directives" / "_parked_orphan_chain"
    pend.mkdir(parents=True, exist_ok=True)
    return repo, pend, parked


# --- POLE B: orphaned dependent (no producer) is PARKED -----------------------

def test_orphan_without_producer_is_parked(tmp_path):
    repo, pend, parked = _setup(tmp_path)
    # router requires logic.py; NO logic directive, logic.py absent on disk -> orphan
    _mk(pend, "router.json",
        requires=["services/staged/svc/logic.py"],
        output_file="services/staged/svc/router.py")
    p = poc.park_orphan_chains(repo=repo, pending_dir=pend, parked_dir=parked, apply=True, log=lambda *a: None)
    assert [n for n, _ in p["park"]] == ["router.json"]
    assert not (pend / "router.json").exists()
    assert (parked / "router.json").exists()


# --- POLE A: dependent WITH a producer in pending/ is KEPT (just waiting) ------

def test_dependent_with_producer_is_kept(tmp_path):
    repo, pend, parked = _setup(tmp_path)
    # logic producer IS present -> router is merely WAITING, not orphaned -> keep both
    _mk(pend, "logic.json", requires=[], output_file="services/staged/svc/logic.py")
    _mk(pend, "router.json",
        requires=["services/staged/svc/logic.py"],
        output_file="services/staged/svc/router.py")
    p = poc.park_orphan_chains(repo=repo, pending_dir=pend, parked_dir=parked, apply=True, log=lambda *a: None)
    assert p["park"] == []
    assert (pend / "router.json").exists() and (pend / "logic.json").exists()


# --- requirement already on disk is KEPT --------------------------------------

def test_requirement_satisfied_on_disk_is_kept(tmp_path):
    repo, pend, parked = _setup(tmp_path)
    (repo / "services" / "staged" / "svc").mkdir(parents=True)
    (repo / "services" / "staged" / "svc" / "logic.py").write_text("x", encoding="utf-8")
    _mk(pend, "router.json",
        requires=["services/staged/svc/logic.py"],
        output_file="services/staged/svc/router.py")
    p = poc.park_orphan_chains(repo=repo, pending_dir=pend, parked_dir=parked, apply=True, log=lambda *a: None)
    assert p["park"] == []
    assert (pend / "router.json").exists()


# --- REVIVE: a parked directive becomes buildable -> moved back ---------------

def test_parked_is_revived_when_requirement_appears(tmp_path):
    repo, pend, parked = _setup(tmp_path)
    _mk(parked, "router.json",
        requires=["services/staged/svc/logic.py"],
        output_file="services/staged/svc/router.py")
    # the requirement now exists on disk -> revive
    (repo / "services" / "staged" / "svc").mkdir(parents=True)
    (repo / "services" / "staged" / "svc" / "logic.py").write_text("x", encoding="utf-8")
    p = poc.park_orphan_chains(repo=repo, pending_dir=pend, parked_dir=parked, apply=True, log=lambda *a: None)
    assert [n for n, _ in p["revive"]] == ["router.json"]
    assert (pend / "router.json").exists()
    assert not (parked / "router.json").exists()


# --- dry-run writes nothing ---------------------------------------------------

def test_dry_run_moves_nothing(tmp_path):
    repo, pend, parked = _setup(tmp_path)
    _mk(pend, "router.json",
        requires=["services/staged/svc/logic.py"],
        output_file="services/staged/svc/router.py")
    p = poc.park_orphan_chains(repo=repo, pending_dir=pend, parked_dir=parked, apply=False, log=lambda *a: None)
    assert [n for n, _ in p["park"]] == ["router.json"]
    assert (pend / "router.json").exists()          # NOT moved in dry-run
    assert not (parked / "router.json").exists()
