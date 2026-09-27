"""Two poles for fire_gate's migration-OWNERSHIP fold.

The pole that matters is RED: an ownership failure must turn a SAFE delta into
RESTAGE. A test that only shows GREEN passing is a rubber stamp -- the whole
reason this apparatus exists is that `sha_green.py` and then
`migration_owner_probe.py` both sat with zero callers while every gate was green.

Pure over apply_owner (no DB, no network), plus the engagement condition, which is
what makes a Class A delta byte-identical to the pre-patch behaviour.
"""
import pathlib
import sys

import pytest

TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import fire_gate  # noqa: E402


# ----------------------------------------------------------------- engagement
def test_class_a_delta_does_not_engage_the_probe():
    """No migration in the delta -> NOT ENGAGED. This is what keeps the common
    (Class A) path identical to before the probe was wired in."""
    changed = ["zo_sentinel/app.py", "tools/reachability_ratchet.py", "README.md"]
    assert fire_gate.migration_paths_in_delta(changed) == []
    owner = fire_gate.migration_owner("rob531/zo-sentinel", "a" * 40, "b" * 40, changed)
    assert owner["verdict"] == "NOT-ENGAGED"
    assert owner["rc"] is None
    assert owner["migrations"] == []


def test_migration_in_delta_is_detected_on_both_separators():
    changed = ["migrations/versions/0013_registry_stable_repo_id.py",
               "migrations\\versions\\0014_something.py",
               "migrations/env.py",            # not a revision
               "migrations/versions/README"]   # not .py
    got = fire_gate.migration_paths_in_delta(changed)
    assert len(got) == 2, got


# ----------------------------------------------------------------- both poles
def test_owner_red_turns_safe_into_restage():
    """THE RED POLE. Without this the fold is decorative."""
    verdict, forced = fire_gate.apply_owner("SAFE", {"verdict": "RED", "rc": 1})
    assert verdict == "RESTAGE"
    assert forced is True


def test_owner_green_leaves_the_verdict_alone():
    verdict, forced = fire_gate.apply_owner("SAFE", {"verdict": "GREEN", "rc": 0})
    assert verdict == "SAFE"
    assert forced is False


@pytest.mark.parametrize("owner", [
    {"verdict": "UNKNOWN", "rc": 2},
    {"verdict": "NOT-ENGAGED", "rc": None},
    {"verdict": "SKIPPED", "rc": None},
])
def test_unknown_and_skip_are_not_red(owner):
    """R6: unknown is not red. An instrument that cannot answer must not become a
    blocker (R7) -- that is how gates that can only go red get built."""
    verdict, forced = fire_gate.apply_owner("SAFE", owner)
    assert verdict == "SAFE"
    assert forced is False


def test_owner_red_cannot_rescue_an_already_restaged_verdict():
    """The fold only ever tightens SAFE; it must not rewrite an existing RESTAGE
    (which would let one signal mask another)."""
    verdict, forced = fire_gate.apply_owner("RESTAGE", {"verdict": "RED", "rc": 1})
    assert verdict == "RESTAGE"
    assert forced is False


# ------------------------------------------------------- probe is REACHABLE
def test_probe_resolves_or_reports_unavailable_never_a_pass():
    """Absent must land as UNKNOWN, never as GREEN. This is the sentinel-value
    join that the `unattributed` incident got wrong in the other direction."""
    probe = fire_gate.resolve_owner_probe()
    changed = ["migrations/versions/0013_registry_stable_repo_id.py"]
    if probe is None:
        owner = fire_gate.migration_owner("rob531/zo-sentinel", "a" * 40, "b" * 40, changed)
        assert owner["verdict"] == "UNKNOWN"
        assert owner["rc"] == 2
        assert owner["source"] == "unavailable"
    else:
        assert probe.is_file()
        assert probe.name == "migration_owner_probe.py"
