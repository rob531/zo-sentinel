"""Negative controls for the Gate 8 accounting keyspace (FU-233 follow-on).

R4: an assertion never seen RED is not evidence. Each test here states the
OLD behaviour explicitly and asserts it is gone, so the test would have failed
against the pre-2026-08-03 code rather than passing vacuously.

THE DEFECT. `gate_8_new_module` counted build failures under
`Path(build['file']).name`. Every service unit emits the same five filenames,
so that key summed unrelated artifacts:

  services/staged/a/__init__.py  ->  "__init__.py"
  services/staged/b/__init__.py  ->  "__init__.py"   <-- same counter

Against a retry budget of 3 this reached 19 attempts and quarantined
`__init__.py` globally, which `may_rebuild()` then returned False for -- and
`sentinel_directive_generator.validate_directive()` rejects on that, so the
atomic unit of the loop was unbuildable. The same truncation collapsed cohort
SETS, producing `size=1 fail=100%` cohorts that tripped the breaker.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "gates"))


# --------------------------------------------------------------------------
# 1. The key must identify ONE artifact.
# --------------------------------------------------------------------------

def _key(file_value):
    import gate_quality_state  # noqa: F401  (import side-effect ordering)
    from gates import gate_8_new_module as g8
    return g8._identity_key({"file": file_value})


def test_two_services_do_not_share_a_counter():
    """NEGATIVE CONTROL. Under the old keying both sides were '__init__.py'
    and this assertion FAILED. If it ever passes trivially, check that
    _identity_key is still being called by the accounting loop."""
    a = _key("services/staged/alpha/__init__.py")
    b = _key("services/staged/beta/__init__.py")
    assert a != b, "two distinct services still share one accounting key"
    assert Path(a).name == Path(b).name == "__init__.py", (
        "precondition: the basenames ARE identical -- that is the whole "
        "reason the basename cannot be the key"
    )


@pytest.mark.parametrize("member", [
    "service.toml", "__init__.py", "router.py", "logic.py", "contract.py",
])
def test_every_service_unit_member_is_disambiguated(member):
    assert _key(f"services/staged/x/{member}") != _key(f"services/staged/y/{member}")


def test_flat_legacy_module_key_is_unchanged():
    """No historical entry may be orphaned: a module built at the root still
    keys to its bare basename, exactly as before."""
    assert _key("mcp_scanner.py") == "mcp_scanner.py"
    assert _key("/home/workspace/zo_sentinel/mcp_scanner.py") == "mcp_scanner.py"


def test_unrelativisable_absolute_path_keeps_full_path_not_basename():
    """R6: unknown is not zero. A path we cannot relativise is still an
    identity; its basename is not, so we must not silently fall back to it."""
    k = _key("/some/other/root/router.py")
    assert k == "/some/other/root/router.py"
    assert k != "router.py"


def test_empty_or_missing_file_field_yields_empty_key():
    assert _key("") == ""
    from gates import gate_8_new_module as g8
    assert g8._identity_key({}) == ""


# --------------------------------------------------------------------------
# 2. A non-identifying key must not be able to block anything.
# --------------------------------------------------------------------------

@pytest.fixture
def state(tmp_path, monkeypatch):
    import gate_quality_state as gqs
    f = tmp_path / "gate_quality_state.json"
    f.write_text(json.dumps({"state": "closed"}))
    monkeypatch.setattr(gqs, "_resolve_state_file", lambda: f, raising=False)
    monkeypatch.setenv("GATE_QUALITY_STATE_FILE", str(f))
    return gqs, f


def _write(f, obj):
    f.write_text(json.dumps(obj))


def test_bare_member_basename_is_recognised_as_nonidentifying():
    import gate_quality_state as gqs
    for m in gqs.SERVICE_UNIT_MEMBERS:
        assert gqs._is_nonidentifying(m) is True
    # A path-qualified member names one artifact and MUST survive.
    assert gqs._is_nonidentifying("services/staged/foo/router.py") is False
    # A legacy flat module is not a service-unit member and MUST survive.
    assert gqs._is_nonidentifying("mcp_scanner.py") is False
    assert gqs._is_nonidentifying("") is False


def test_poisoned_entries_are_dropped_and_legacy_entries_survive(state):
    """NEGATIVE CONTROL for the migration: the three keys measured live on
    2026-08-03 go away, and the seventeen legitimate 2026-04/05 module
    entries beside them do not."""
    gqs, f = state
    _write(f, {
        "state": "closed",
        "quarantined": {
            "__init__.py": {"reason": "missing_on_disk after 19 fails"},
            "service.toml": {"reason": "missing_on_disk after 15 fails"},
            "router.py": {"reason": "missing_on_disk after 5 fails"},
            "e2e_scenarios.py": {"reason": "3 consecutive fails"},
            "services/staged/foo/router.py": {"reason": "3 consecutive fails"},
        },
        "file_retries": {
            "__init__.py": {"attempts": 19},
            "mcp_scanner.py": {"attempts": 2},
        },
    })
    dropped = gqs.drop_nonidentifying_keys()
    snap = gqs.snapshot()
    assert set(snap["quarantined"]) == {
        "e2e_scenarios.py", "services/staged/foo/router.py"}
    assert set(snap["file_retries"]) == {"mcp_scanner.py"}
    assert len(dropped) == 4


def test_drop_is_idempotent(state):
    """Protean: safely re-runnable, converges rather than duplicating."""
    gqs, f = state
    _write(f, {"state": "closed",
               "quarantined": {"service.toml": {"reason": "missing_on_disk"}}})
    first = gqs.drop_nonidentifying_keys()
    second = gqs.drop_nonidentifying_keys()
    assert first and second == []
    assert gqs.snapshot()["quarantined"] == {}


def test_may_rebuild_no_longer_blocks_the_atomic_unit(state):
    """THE MOTIVATING INCIDENT (R: a detector's first proof must be the
    incident). Measured live 2026-08-03T12:0xZ, before this change:
        may_rebuild('service.toml') -> (False, 'quarantined ... 15 fails')
    """
    gqs, f = state
    _write(f, {"state": "closed", "quarantined": {
        "service.toml": {"quarantined_at": "2026-08-03T09:35:32Z",
                         "reason": "missing_on_disk after 15 fails"}}})
    ok, reason = gqs.may_rebuild("service.toml")
    assert ok is True, f"atomic unit still blocked: {reason}"


def test_a_real_quarantine_still_blocks(state):
    """The fix must REMOVE a false gate, not every gate. A path-qualified
    entry -- one that does identify an artifact -- must still block."""
    gqs, f = state
    _write(f, {"state": "closed", "quarantined": {
        "services/staged/foo/router.py": {"quarantined_at": "2026-08-03T00:00:00Z",
                                          "reason": "3 consecutive fails"}}})
    ok, _ = gqs.may_rebuild("services/staged/foo/router.py")
    assert ok is False
    ok2, _ = gqs.may_rebuild("e2e_scenarios.py")
    assert ok2 is True
