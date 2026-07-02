"""CROSS-HOP integration tests: policy -> refill -> gaps extractor ->
promoter(+janitor) -> engine -> janitor, on REAL files in one layout.

Each unit suite proves its own hop; these tests prove the HANDOFFS -- that
what one hop emits is exactly what the next hop consumes, under one policy
posture. A regression in any seam (file naming, candidate syntax, gate
resolution, retirement classes) fails HERE even if every unit suite stays
green. Includes edge cases at each seam.

Hops covered (upstream -> downstream):
  KL design doc --mine--> AUTO_ANCHOR --extract--> candidate set
  proposal file --promoter--> pending (janitor policy-gated in the same cycle)
  ghost directive --engine--> declared output on disk
  output on disk --janitor--> pending retirement + promoter .duplicate archive

goose_runner itself cannot be imported hermetically (import-time host paths);
its policy hop is the same policy.flag() call proven here and its fallback
branch is the pre-policy code covered by history. That boundary is explicit.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from zo_sentinel import anchor_refill, engine_build, policy, queue_janitor  # noqa: E402
from zo_sentinel.promoters import proposed_to_pending_promoter as promoter  # noqa: E402


class FakeResp:
    def __init__(self, content, status=200):
        self.status_code = status
        self._c = content

    def json(self):
        return {"choices": [{"message": {"content": self._c}}]}


PASSING = ("def f():\n    return 1\n\n"
           "if __name__ == '__main__':\n    assert f() == 1\n    print('PASS')\n")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """One coherent sentinel world: home, directives, lessons, docs,
    durable quarantine, isolated policy override, no env leakage."""
    home = tmp_path / "home"
    for sub in ("directives/proposed", "directives/pending", "lessons", "docs"):
        (home / sub).mkdir(parents=True)
    quarantine = tmp_path / "state" / "quarantine"
    quarantine.mkdir(parents=True)
    monkeypatch.setenv("ZO_DURABLE_QUARANTINE_DIR", str(quarantine))
    monkeypatch.setenv("ZO_POLICY_OVERRIDE_PATH",
                       str(tmp_path / "state" / "policy_override.json"))
    for meta in policy.KEYS.values():
        if meta.get("env"):
            monkeypatch.delenv(meta["env"], raising=False)
    return {"home": home, "directives": home / "directives",
            "quarantine": quarantine}


def _directive(task, output_file=None):
    return {"task": task,
            "output_file": output_file if output_file is not None else f"{task}.py",
            "handler": "generate_file", "description": "x" * 60}


def _write(d, name, directive):
    p = d / name
    p.write_text(json.dumps(directive), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# HOP CHAIN 1: KL doc -> refill -> AUTO_ANCHOR -> gaps extractor -> exclusion
# ---------------------------------------------------------------------------

def test_chain_kl_doc_to_candidate_to_disk_exclusion(world):
    """A module named in a design doc becomes a candidate the gaps extractor
    sees; once the file EXISTS on disk it stops being 'missing' -- and the
    refill never re-mines it. Upstream (doc) and downstream (extractor+disk)
    agree at every step."""
    home = world["home"]
    (home / "PRODUCT_SPEC.md").write_text(
        "## Appendix (directive candidates, NOT YET BUILT)\n"
        "- directive candidate: `existing_thing.py` -- built already.\n",
        encoding="utf-8")
    (home / "existing_thing.py").write_text("# built\n", encoding="utf-8")
    (home / "docs" / "DESIGN_CHAIN.md").write_text(
        "The next phase needs `chain_alert_service.py` which watches tier\n"
        "changes and writes alert rows via write_service.\n", encoding="utf-8")

    # anchor low (0 missing) -> refill mines the doc
    stats = anchor_refill.run_refill(home, quarantine_dir=world["quarantine"])
    assert stats["reason"] == "refilled"
    assert stats["files"] == ["chain_alert_service.py"]

    # downstream: the extractor sees the mined candidate as missing work
    combined = ((home / "PRODUCT_SPEC.md").read_text(encoding="utf-8") + "\n" +
                (home / anchor_refill.AUTO_ANCHOR_NAME).read_text(encoding="utf-8"))
    cands = anchor_refill.spec_candidate_files(combined)
    assert "chain_alert_service.py" in cands

    # the module gets BUILT -> next refill treats the anchor as satisfied by
    # NEW work only and never re-mines the built name
    (home / "chain_alert_service.py").write_text(PASSING, encoding="utf-8")
    again = anchor_refill.run_refill(home, quarantine_dir=world["quarantine"])
    assert again["appended"] == 0
    assert "chain_alert_service.py" not in again["files"]


def test_chain_policy_threshold_controls_refill(world):
    """Policy override (not code, not env) changes refill behavior live."""
    home = world["home"]
    (home / "PRODUCT_SPEC.md").write_text(
        "candidates:\n"
        "- directive candidate: `missing_a.py` -- a\n"
        "- directive candidate: `missing_b.py` -- b\n", encoding="utf-8")
    (home / "docs" / "DESIGN_T.md").write_text(
        "needs `threshold_mod.py` for the next phase\n", encoding="utf-8")

    policy.set_override("architect.refill_threshold", 1)  # 2 missing >= 1
    s1 = anchor_refill.run_refill(home, quarantine_dir=world["quarantine"])
    assert s1["reason"] == "anchor_sufficient"

    policy.set_override("architect.refill_threshold", 10)  # 2 missing < 10
    s2 = anchor_refill.run_refill(home, quarantine_dir=world["quarantine"])
    assert s2["reason"] == "refilled" and s2["files"] == ["threshold_mod.py"]


# ---------------------------------------------------------------------------
# HOP CHAIN 2: engine writes output -> janitor retires the pending directive
#              -> promoter archives the re-proposal. Three modules, one truth.
# ---------------------------------------------------------------------------

def test_chain_engine_output_feeds_janitor_and_promoter(world):
    """The engine's declared-output write is EXACTLY the artifact the janitor's
    redundancy rule and the promoter cycle key on -- no seam drift."""
    home, directives = world["home"], world["directives"]
    d = _directive("build_chain_widget", "chain_widget.py")

    # ghost retry lands in pending; a re-proposal of the same work is queued
    pending_file = _write(directives / "pending", "gen_1_build_chain_widget.json", d)
    reproposal = _write(directives / "proposed", "gen_1_build_chain_widget.json", d)

    # HOP: engine builds the declared output (grounded, fake shim)
    res = engine_build.build_with_engine(
        d, "build the chain widget", home=str(home),
        post=lambda url, **kw: FakeResp(f"```python\n{PASSING}```"),
        log=lambda *a: None)
    assert res["success"] is True
    assert (home / "chain_widget.py").is_file()

    # HOP: one promoter cycle (janitor ON via declared posture) -- the janitor
    # retires the now-redundant pending squatter using the SAME declared-output
    # rule the engine wrote against...
    promoter.run_once(directives / "proposed", directives / "pending",
                      min_age_secs=0, max_per_cycle=10,
                      directives_root=directives)
    assert not pending_file.exists()
    retired = list((directives / "retired").rglob("gen_1_build_chain_widget.json"))
    # BOTH copies of the now-redundant work retire in one pass: the pending
    # squatter AND the proposed re-proposal, each into its own class dir.
    assert len(retired) == 2
    classes = {p.parent.name for p in retired}
    assert classes == {"pending_redundant", "proposed_redundant"}

    # ...and the re-proposal was NOT promoted into the freed slot as a rebuild:
    # it was retired from proposed/ by the janitor in the same pass (redundant
    # at the source), or archived -- either way pending stays clean.
    assert not (directives / "pending" / "gen_1_build_chain_widget.json").exists()
    assert not reproposal.exists()


def test_chain_live_flip_changes_next_cycle_without_restart(world):
    """Operational property the whole consolidation exists for: one policy
    write flips a hop's behavior on the NEXT cycle, no process restart."""
    home, directives = world["home"], world["directives"]
    (home / "flip_mod.py").write_text(PASSING, encoding="utf-8")
    squatter = _write(directives / "pending", "gen_2_build_flip_mod.json",
                      _directive("build_flip_mod", "flip_mod.py"))

    policy.set_override("queue.janitor", False)
    promoter.run_once(directives / "proposed", directives / "pending",
                      min_age_secs=0, max_per_cycle=10, directives_root=directives)
    assert squatter.exists()                       # gate off -> untouched

    policy.set_override("queue.janitor", True)     # the live flip
    promoter.run_once(directives / "proposed", directives / "pending",
                      min_age_secs=0, max_per_cycle=10, directives_root=directives)
    assert not squatter.exists()                   # same process, new behavior


# ---------------------------------------------------------------------------
# Edge cases at the seams
# ---------------------------------------------------------------------------

def test_edge_engine_nested_output_path(world):
    """Declared outputs in nested dirs (app/routers/x.py) must write cleanly
    -- the janitor's declared_output resolution uses the same path logic."""
    d = _directive("build_nested_api", "app/routers/nested_api.py")
    res = engine_build.build_with_engine(
        d, "t", home=str(world["home"]),
        post=lambda url, **kw: FakeResp(PASSING), log=lambda *a: None)
    assert res["success"] is True
    assert (world["home"] / "app" / "routers" / "nested_api.py").is_file()


def test_edge_unknown_override_keys_are_inert(world):
    """A future/typo'd key in the override file must never break resolution."""
    policy.set_override("queue.janitor", True)
    p = pathlib.Path(policy._override_path())
    data = json.loads(p.read_text(encoding="utf-8"))
    data["future.unknown_knob"] = 42
    p.write_text(json.dumps(data), encoding="utf-8")
    assert policy.flag("queue.janitor", world["directives"]) is True


def test_edge_sentinel_content_variants(world):
    """Legacy sentinels with whitespace / case variants keep their meaning."""
    sf = world["directives"] / ".queue_janitor_on"
    for content, expected in ((" 1 \n", True), ("TRUE", True), ("Off\n", False),
                              ("false", False), ("", False)):
        sf.write_text(content, encoding="utf-8")
        assert policy.flag("queue.janitor", world["directives"]) is expected, content


def test_edge_janitor_never_touches_unrelated_files(world):
    """Non-directive artifacts sharing the queue dirs survive a full pass."""
    directives = world["directives"]
    keep = [
        (directives / "pending" / "note.txt", "not json"),
        (directives / "pending" / "x.json.duplicate", "{}"),
        (directives / "proposed" / "y.json.rejected", "{}"),
        (directives / "pending" / "old.failed.json", "{}"),
    ]
    for p, content in keep:
        p.write_text(content, encoding="utf-8")
    stats = queue_janitor.run_pass(directives,
                                   quarantine_dirs=[directives, world["quarantine"]])
    assert stats["retired"] == 0
    assert all(p.exists() for p, _ in keep)


def test_edge_engine_unicode_and_crlf_content(world):
    """Model output with unicode + CRLF must round-trip to a compiling file."""
    code = ("# -*- coding: utf-8 -*-\r\n# naïve café ☂\r\nX = 'ünïcode'\r\n\r\n"
            "if __name__ == '__main__':\r\n    assert X\r\n    print('PASS')\r\n")
    d = _directive("build_uni_mod", "uni_mod.py")
    res = engine_build.build_with_engine(
        d, "t", home=str(world["home"]),
        post=lambda url, **kw: FakeResp(code), log=lambda *a: None)
    assert res["success"] is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
