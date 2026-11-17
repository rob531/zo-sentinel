"""test_promoter_chain_root_gate.py -- the chain-root promotion gate.

A service fan-out emits logic -> router -> {contract, service.toml}, each dependent
`requires` the prior file. The promoter must NOT move a dependent into pending/ until
its chain-root exists on disk, else it defers forever and clogs the queue (the
orphaned-chain stall). It holds the dependent in proposed/ and promotes it once the
leaf builds.

Two poles:
  * HELD: requires names a file not on disk -> "deferred_requires", stays in proposed/.
  * PROMOTED: requires satisfied on disk (or a leaf with no requires) -> moves to pending/.
Hermetic: REPO_ROOT monkeypatched to tmp so requires resolve against the fixture.
"""
from __future__ import annotations

import json
from pathlib import Path

from zo_sentinel.promoters import proposed_to_pending_promoter as promoter

_DESC = ("A sufficiently long description that satisfies the 50-char minimum "
         "validation rule used by the promoter.")


def _dirs(tmp_path):
    proposed = tmp_path / "proposed"
    pending = tmp_path / "pending"
    proposed.mkdir()
    pending.mkdir()
    return proposed, pending


def _directive(output_file: str, requires):
    return {
        "task": "build_" + output_file.replace("/", "_").replace(".", "_"),
        "handler": "generate_file",
        "output_file": output_file,
        "complexity": "medium",
        "description": _DESC,
        "source": "directive_architect",
        "requires": requires,
    }


def _write(proposed: Path, name: str, d: dict) -> Path:
    p = proposed / name
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


# --- POLE HELD: dependent with its chain-root absent stays in proposed/ -------

def test_dependent_held_when_root_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(promoter, "REPO_ROOT", tmp_path)
    proposed, pending = _dirs(tmp_path)
    src = _write(proposed, "svc_services_staged_svc_router_py.json",
                 _directive("services/staged/svc/router.py",
                            ["services/staged/svc/logic.py"]))     # logic.py NOT on disk
    c = promoter.PromotionCounters()
    out = promoter._do_promote(src, pending, dry_run=False, counters=c)
    assert out == "deferred_requires"
    assert src.exists()                                            # held in proposed/
    assert not (pending / src.name).exists()
    assert c.promoted == 0 and c.skipped == 1


# --- POLE PROMOTED: chain-root on disk -> dependent promotes -------------------

def test_dependent_promotes_once_root_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(promoter, "REPO_ROOT", tmp_path)
    proposed, pending = _dirs(tmp_path)
    (tmp_path / "services" / "staged" / "svc").mkdir(parents=True)
    (tmp_path / "services" / "staged" / "svc" / "logic.py").write_text("x", encoding="utf-8")
    src = _write(proposed, "svc_services_staged_svc_router_py.json",
                 _directive("services/staged/svc/router.py",
                            ["services/staged/svc/logic.py"]))     # now exists
    c = promoter.PromotionCounters()
    out = promoter._do_promote(src, pending, dry_run=False, counters=c)
    assert out == "promoted"
    assert (pending / src.name).exists()
    assert not src.exists()


# --- leaf with no requires always promotes (the logic root itself) ------------

def test_leaf_promotes(tmp_path, monkeypatch):
    monkeypatch.setattr(promoter, "REPO_ROOT", tmp_path)
    proposed, pending = _dirs(tmp_path)
    src = _write(proposed, "svc_services_staged_svc_logic_py.json",
                 _directive("services/staged/svc/logic.py", []))   # leaf, no requires
    c = promoter.PromotionCounters()
    out = promoter._do_promote(src, pending, dry_run=False, counters=c)
    assert out == "promoted"
    assert (pending / src.name).exists()


def test_unmet_requires_helper(tmp_path, monkeypatch):
    monkeypatch.setattr(promoter, "REPO_ROOT", tmp_path)
    (tmp_path / "there.py").write_text("x", encoding="utf-8")
    assert promoter._unmet_requires({"requires": ["there.py"]}) == []
    assert promoter._unmet_requires({"requires": ["missing.py"]}) == ["missing.py"]
    assert promoter._unmet_requires({}) == []                      # leaf
