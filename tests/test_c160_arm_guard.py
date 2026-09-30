"""cycle-0160 / FU-572 -- ops/host/arm_guard.sh must keep its four properties.

Each test asserts a property AND asserts that the mutant which violates it is
actually caught, so a revert of the guard fails this suite rather than passing
quietly. The call-site control is deliberately NOT here: it reads the live host
crontab (R1) and has no meaning on a CI runner -- it lives in
tools/c160_arm_guard_evidence.py --controls, which is run on the host.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVID = ROOT / "tools" / "c160_arm_guard_evidence.py"
GUARD = ROOT / "ops" / "host" / "arm_guard.sh"


def _load():
    spec = importlib.util.spec_from_file_location("c160_evidence", EVID)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["c160_evidence"] = mod          # before exec_module, per c73
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def evid():
    assert EVID.exists(), f"{EVID} missing"
    return _load()


def test_the_guard_is_tracked_and_executable_bash(evid):
    assert GUARD.exists(), "ops/host/arm_guard.sh is missing"
    src = GUARD.read_text()
    assert src.startswith("#!/usr/bin/env bash")
    assert shutil.which("bash"), "bash unavailable; this guard is a bash script"


@pytest.mark.parametrize(
    "control_name",
    [
        "heartbeat-written-on-the-no-op-path",
        "safe_ff-resolved-from-the-tracked-ref",
        "a-still-behind-host-is-not-reported-armed",
        "a-failed-fetch-is-unknown-not-current",
    ],
)
def test_control_passes_on_the_real_guard(evid, control_name):
    fn, _ = evid.CONTROLS[control_name]
    with tempfile.TemporaryDirectory() as td:
        ok, why = fn(GUARD, Path(td))
    assert ok, f"{control_name} failed on the real guard: {why}"


@pytest.mark.parametrize(
    "control_name",
    [
        "heartbeat-written-on-the-no-op-path",
        "safe_ff-resolved-from-the-tracked-ref",
        "a-still-behind-host-is-not-reported-armed",
        "a-failed-fetch-is-unknown-not-current",
    ],
)
def test_the_mutant_is_observed_red(evid, control_name):
    """R4. A control that its own mutant passes is not evidence."""
    fn, mutant_key = evid.CONTROLS[control_name]
    assert mutant_key, "this control has no mutant"
    desc, mutate = evid.MUTANTS[mutant_key]
    real = GUARD.read_text()
    mutated = mutate(real)
    assert mutated != real, (
        f"mutant '{mutant_key}' did not apply -- the control is vacuous, which "
        f"is worse than absent because it looks rigorous"
    )
    with tempfile.TemporaryDirectory() as td:
        mpath = Path(td) / "mutant.sh"
        mpath.write_text(mutated)
        ok, why = fn(mpath, Path(td))
    assert not ok, (
        f"mutant '{mutant_key}' ({desc}) PASSED control '{control_name}' -- "
        f"the control does not measure what it claims"
    )


def test_the_guard_is_not_a_gate(evid):
    """META CAP: this file gives an existing repair a trigger. If it ever grows
    the power to refuse something, that is a different object needing a
    different justification."""
    src = GUARD.read_text()
    for forbidden in ("exit 1", "--enforce", "required", "block"):
        assert forbidden not in src, (
            f"arm_guard.sh contains {forbidden!r} -- it is becoming a gate"
        )


def test_it_is_a_noop_when_current(evid):
    """Idempotence by character: re-running an armed host changes nothing."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _, runtime = evid.build_fixture(tmp, behind=False)
        hb = tmp / "hb.json"
        first = evid.run_guard(GUARD, runtime, hb)
        assert first.returncode == 0, first.stderr
        head1 = evid.sh(["git", "-C", str(runtime), "rev-parse", "HEAD"]).stdout
        second = evid.run_guard(GUARD, runtime, hb)
        assert second.returncode == 0
        head2 = evid.sh(["git", "-C", str(runtime), "rev-parse", "HEAD"]).stdout
        assert head1 == head2, "a second run moved HEAD on an already-current repo"
