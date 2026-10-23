"""Tests for ops/host/pipeline_liveness_guard.py (GH #3415 prevention 3, FU-349).

The guard is outcome-based: it must ALARM when live pending work exists and no
directive has completed within the threshold, stay silent when completions are
flowing or the queue is empty, and refuse to guess (rc=2) when the tree is
absent. Both directions are exercised -- an assertion never seen fail is not
evidence (HARNESS_DOCTRINE R4).
"""
import importlib.util
import json
import os
import time

import pytest

_GUARD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "ops", "host", "pipeline_liveness_guard.py")


@pytest.fixture()
def guard(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("pipeline_liveness_guard", _GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.LOG = str(tmp_path / "guard.log")
    mod.STALL_SENTINEL = str(tmp_path / "PIPELINE_STALLED")
    return mod


@pytest.fixture()
def tree(tmp_path):
    root = tmp_path / "directives"
    (root / "pending").mkdir(parents=True)
    return root


def _touch(path, age_s=0.0):
    path.write_text("{}")
    if age_s:
        t = time.time() - age_s
        os.utime(path, (t, t))


def test_empty_pending_is_healthy(guard, tree):
    _touch(tree / "ancient.done.json", age_s=48 * 3600)
    assert guard.check(str(tree), 2.0) == 0


def test_stale_completions_alarm_and_latch(guard, tree):
    _touch(tree / "pending" / "d1.json")
    _touch(tree / "old.done.json", age_s=3 * 3600)
    assert guard.check(str(tree), 2.0) == 1
    latch = json.loads(open(guard.STALL_SENTINEL).read())
    assert latch["event"] == "PIPELINE_STALLED"
    assert latch["live_pending"] == 1


def test_fresh_completion_is_healthy_and_clears_latch(guard, tree):
    _touch(tree / "pending" / "d1.json")
    _touch(tree / "old.done.json", age_s=3 * 3600)
    assert guard.check(str(tree), 2.0) == 1
    _touch(tree / "fresh.done.json")
    assert guard.check(str(tree), 2.0) == 0
    assert not os.path.exists(guard.STALL_SENTINEL)


def test_no_done_sentinel_at_all_alarms(guard, tree):
    _touch(tree / "pending" / "d1.json")
    assert guard.check(str(tree), 2.0) == 1


def test_terminal_suffixes_do_not_count_as_live_pending(guard, tree):
    # A directory listing is not a queue depth (FU-169): terminal/audit files
    # in pending/ must not inflate the live count.
    for sfx in (".done.json", ".failed.json", ".rejected", ".duplicate",
                ".expanded", ".revived"):
        _touch(tree / "pending" / ("x%s" % sfx))
    assert guard.check(str(tree), 2.0) == 0


def test_failed_sentinels_are_context_not_completions(guard, tree):
    # A pipeline failing everything is not a pipeline completing work: a fresh
    # .failed.json must NOT satisfy the completion test.
    _touch(tree / "pending" / "d1.json")
    _touch(tree / "fresh.failed.json")
    assert guard.check(str(tree), 2.0) == 1


def test_missing_tree_is_cannot_evaluate(guard, tmp_path):
    assert guard.check(str(tmp_path / "nope"), 2.0) == 2


def test_selftest_passes(guard):
    assert guard.selftest() == 0
