"""Collect cadence_health's self-test AND its mutation matrix into the REQUIRED check.

A self-test that runs only when someone types the command is a habit, not a
check -- the lesson test_vast_spend_selftest.py already carries, and the reason
64% of this suite had never once run. So both go in the gate:

  * `selftest()` -- 14 offline assertions, both poles, plus every false-green
    path measured live on 2026-09-13.
  * the MUTATION MATRIX -- the R4 proof that each guard carries a real
    assertion. This is the part worth gating. The matrix found a dead guard in
    the tool on its first run; without it in CI, a future refactor can quietly
    re-hollow a guard and every green stays green.

Network-free: every path uses an in-module fixture. The one live-ish path is
pointed at 127.0.0.1:9, which must REFUSE -- and that refusal IS the assertion,
because an unreachable cadence host has to raise, never degrade to GREEN.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "cadence_health.py"
MATRIX_PATH = Path(__file__).resolve().parents[1] / "tools" / "cadence_mutation_control.py"


def _load():
    sys.path.insert(0, str(MODULE_PATH.parent))
    spec = importlib.util.spec_from_file_location("cadence_health", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cadence_health"] = mod       # BEFORE exec_module, or dataclasses dies
    spec.loader.exec_module(mod)
    return mod


def test_cadence_health_selftest_passes():
    assert _load().selftest() == 0


def test_a_401_body_is_unknown_not_green():
    """The defect this tool exists for, measured live 2026-09-13: a junk key
    returns HTTP 401 {"detail":"Authentication required"}, and the hand-rolled
    reader called that GREEN. FU-192's false-zero class one surface over."""
    ch = _load()
    try:
        ch.evaluate({"detail": "Authentication required"})
    except ch.CadenceError as exc:
        assert "sla_hours" in str(exc)
    else:
        raise AssertionError("a 401 body evaluated instead of raising")


def test_the_list_shape_cannot_silently_inspect_nothing():
    """`jobs` is a DICT keyed by name. A reader written for a list iterates KEYS,
    inspects zero job objects, and can never see an overdue job -- green while
    structurally blind. The guard must REFUSE, never degrade to []."""
    ch = _load()
    try:
        ch.normalise_jobs(["perspective_snapshots", "ask_corpus_drift"])
    except ch.CadenceError as exc:
        assert "reads as GREEN while blind" in str(exc)
    else:
        raise AssertionError("list-shaped jobs normalised instead of raising")

    # POSITIVE CONTROL -- it must still accept the real shape, or it is a
    # rubber stamp that refuses everything.
    jobs = ch.normalise_jobs({"ask_corpus_drift": {"overdue": False}})
    assert len(jobs) == 1 and jobs[0]["name"] == "ask_corpus_drift"


def test_an_unreachable_cadence_host_is_unknown_not_green():
    """R6. If the host cannot be read the answer is UNKNOWN. Reporting GREEN
    there would rebuild the defect one derivative up."""
    ch = _load()
    try:
        ch.fetch_health(key="x", url="http://127.0.0.1:9/health")
    except ch.CadenceError as exc:
        assert "unreachable" in str(exc)
    else:
        raise AssertionError("fetch_health returned instead of raising")


def test_unknown_never_maps_to_a_passing_exit_code():
    ch = _load()
    assert ch.rc_for({"verdict": "AUTHENTICATED_GREEN"}) == 0
    assert ch.rc_for({"verdict": "RED"}) == 1
    assert ch.rc_for({"verdict": "UNKNOWN"}) == 2
    assert ch.rc_for({}) == 2          # an absent verdict is UNKNOWN, not green


def test_every_guard_is_observed_carrying_an_assertion():
    """R4 AS A GATE, not as a habit.

    Runs the mutation matrix: each guard is removed from a COPY of the file and
    the mutant is run in a SUBPROCESS (never a module object -- the subject
    reads its own copy off disk). rc 0 means every guard turned --self-test RED
    and the unmutated baseline was GREEN. rc 1 means a guard is decoration;
    rc 2 means a mutation was vacuous or the baseline itself failed, in which
    case no result below it means anything.
    """
    r = subprocess.run([sys.executable, str(MATRIX_PATH)],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, (
        "mutation matrix did not prove every guard:\n%s\n%s"
        % (r.stdout[-3000:], r.stderr[-2000:]))
    assert "R4 GREEN" in r.stdout
