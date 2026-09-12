"""Proves the quarantine re-emission is actually CONSULTED, not merely present.

improvement-loop cycle-0087. `tools/requeue_quarantined.py` sat complete and
CI-tested for 14 days with no caller: every test it had asked "does this tool
work?", and none asked "does anything ever run it?". That gap is the whole
defect, so these tests assert the SEAM, not the tool.

Both poles are exercised deliberately (R4 -- an assertion never seen red is not
evidence): every "does fire" case has a matching "does NOT fire" case, and the
wiring test resolves goose_runner's caller from the PARSED AST, never from raw
text, because a commented-out or string-literal mention reads as an invocation
to a grep (FU-305, the same reason test_graph_gap_directives_wired.py parses).
"""
from __future__ import annotations

import ast
import importlib.util
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MOD_PATH = REPO / "tools" / "quarantine_topup.py"
RUNNER = REPO / "goose_runner.py"
TOOL_REL = "requeue_quarantined.py"


def _load():
    """Import by FILE PATH with sys.modules seeded first -- importing
    `tools.quarantine_topup` normally would drag tools/__init__ and, through
    it, host-only dependencies this runner does not have."""
    spec = importlib.util.spec_from_file_location("quarantine_topup", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["quarantine_topup"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def qt():
    assert MOD_PATH.exists(), "the seam module itself is missing: %s" % MOD_PATH
    return _load()


@pytest.fixture()
def queue(tmp_path):
    pending = tmp_path / "directives" / "pending"
    pending.mkdir(parents=True)
    return pending


def _fill(pending: Path, n: int):
    for i in range(n):
        (pending / ("d%03d.json" % i)).write_text("{}", encoding="utf-8")


# --------------------------------------------------------------- decision ---

def test_fires_when_queue_is_thin_and_never_run(qt, queue):
    ok, why = qt.should_topup(queue, stamp_path=queue.parent / "s")
    assert ok, why


def test_does_not_fire_when_the_queue_is_full(qt, queue):
    """The negative pole of the case above: same code path, opposite world."""
    _fill(queue, qt.QUEUE_FLOOR)
    ok, why = qt.should_topup(queue, stamp_path=queue.parent / "s")
    assert not ok and "floor" in why, why


def test_does_not_fire_inside_the_throttle_window(qt, queue):
    stamp = queue.parent / "s"
    now = time.time()
    stamp.write_text(str(now - 5), encoding="utf-8")
    ok, why = qt.should_topup(queue, stamp_path=stamp, now=now)
    assert not ok and "throttled" in why, why


def test_fires_again_once_the_window_has_passed(qt, queue):
    stamp = queue.parent / "s"
    now = time.time()
    stamp.write_text(str(now - qt.MIN_INTERVAL_S - 1), encoding="utf-8")
    ok, why = qt.should_topup(queue, stamp_path=stamp, now=now)
    assert ok, why


def test_unreadable_stamp_fails_open(qt, queue):
    """R6: an unknown age is not a fresh age. Garbage must not mute the seam."""
    stamp = queue.parent / "s"
    stamp.write_text("not-a-timestamp", encoding="utf-8")
    ok, why = qt.should_topup(queue, stamp_path=stamp)
    assert ok, why


def test_kill_switch_disables_the_seam(qt, queue):
    ok, why = qt.should_topup(queue, stamp_path=queue.parent / "s",
                              env={"ZO_QUARANTINE_TOPUP": "0"})
    assert not ok and "disabled" in why, why


# ------------------------------------------------------------------ spawn ---

def test_topup_invokes_the_quarantine_tool_with_a_capped_limit(qt, queue):
    seen = []
    ok, why = qt.topup(queue, stamp_path=queue.parent / "s",
                       spawn=seen.append)
    assert ok, why
    assert len(seen) == 1, seen
    cmd = seen[0]
    assert any(TOOL_REL in str(part) for part in cmd), cmd
    assert "--emit" in cmd, cmd
    limit = int(cmd[cmd.index("--limit") + 1])
    assert 0 < limit <= 5, "must never RAISE the tool's own MAX_PER_RUN"


def test_topup_spawns_nothing_when_the_decision_is_no(qt, queue):
    _fill(queue, qt.QUEUE_FLOOR)
    seen = []
    ok, _ = qt.topup(queue, stamp_path=queue.parent / "s", spawn=seen.append)
    assert not ok and seen == []


def test_topup_never_raises_when_the_spawn_explodes(qt, queue):
    """It runs inside the builder's poll loop. A top-up that can kill the
    builder is worse than no top-up."""
    def boom(_cmd):
        raise OSError("no such executable")
    ok, why = qt.topup(queue, stamp_path=queue.parent / "s", spawn=boom)
    assert not ok and "failed soft" in why, why


def test_stamp_is_written_so_the_next_poll_is_throttled(qt, queue):
    stamp = queue.parent / "s"
    qt.topup(queue, stamp_path=stamp, spawn=lambda _c: None)
    assert stamp.exists()
    ok, why = qt.should_topup(queue, stamp_path=stamp)
    assert not ok and "throttled" in why, why


# ----------------------------------------------------------------- wiring ---

def _calls_in(tree) -> set:
    """Every dotted callee name in the module, from the AST."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                out.add(f.attr)
            elif isinstance(f, ast.Name):
                out.add(f.id)
    return out


def test_goose_runner_actually_calls_the_seam():
    """THE point of this cycle. Parsed, not grepped: a mention in a comment or
    a docstring is not an invocation."""
    tree = ast.parse(RUNNER.read_text(encoding="utf-8", errors="replace"))
    assert "topup" in _calls_in(tree), (
        "goose_runner.py does not CALL quarantine_topup.topup -- the "
        "re-emission tool is dark again")


def _func(tree, name):
    return next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == name), None)


def test_the_seam_is_reached_from_the_directive_poll_path():
    """Called from somewhere is not called from the path that RUNS. The chain
    load_directives_from_mesh -> topup_quarantine -> topup must be unbroken;
    every builder poll executes the first link."""
    tree = ast.parse(RUNNER.read_text(encoding="utf-8", errors="replace"))
    poll = _func(tree, "load_directives_from_mesh")
    assert poll is not None, "the poll entrypoint was renamed -- re-anchor this test"
    assert "topup_quarantine" in _calls_in(poll), (
        "the top-up is not on the poll path; it would only run if something "
        "else remembered to call it, which is the defect being fixed")
    seam = _func(tree, "topup_quarantine")
    assert seam is not None and "topup" in _calls_in(seam), (
        "topup_quarantine() no longer reaches quarantine_topup.topup")
