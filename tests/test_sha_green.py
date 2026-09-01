"""Tests for tools/sha_green.py -- specifically the REQUIRED-set resolver (FU-206).

These are network-free by construction: `classify_protection` is a pure function
of the branch-protection payload, which is exactly why it was split out of the
`gh api` call. CI can therefore enforce the property that matters without CI
depending on the live protection settings it is asserting about.

THE PROPERTY THESE TESTS EXIST TO PROTECT is not "the seven names are right" --
that changes, in the GitHub UI, outside this repo, which is the whole reason the
literal could not stay authoritative. It is:

    an unusable protection read must never resolve to an EMPTY required set,
    because a gate with nothing to require is a gate that can only go green.

Every assertion here has been seen to FAIL against a deliberately wrong
implementation (one doing `tuple(payload.get("contexts") or [])` with no
empty-check); an assertion never seen red is not evidence.
"""

import importlib.util
import pathlib
import sys

import pytest

_MOD_PATH = pathlib.Path(__file__).resolve().parents[1] / "tools" / "sha_green.py"
_spec = importlib.util.spec_from_file_location("sha_green", _MOD_PATH)
sha_green = importlib.util.module_from_spec(_spec)
sys.modules["sha_green"] = sha_green
_spec.loader.exec_module(sha_green)

FALLBACK = sha_green.REQUIRED_FALLBACK


def test_live_protection_is_adopted_verbatim():
    got, info = sha_green.classify_protection({"contexts": ["alpha", "beta"]})
    assert got == ("alpha", "beta")
    assert info["source"] == "branch_protection"
    assert info["trusted"] is True


def test_a_context_added_upstream_is_picked_up_and_named():
    """The bug this change fixes: a newly-required context the literal never had.

    Under the old hard-coded tuple this context would simply not be checked, and
    a sha that could not merge would be reported GREEN.
    """
    got, info = sha_green.classify_protection(
        {"contexts": list(FALLBACK) + ["licence-scan"]})
    assert "licence-scan" in got
    assert info["drift_vs_literal"]["added"] == ["licence-scan"]
    assert "DRIFT" in info["detail"]


def test_a_context_removed_upstream_is_dropped_and_named():
    got, info = sha_green.classify_protection(
        {"contexts": [c for c in FALLBACK if c != "frontend"]})
    assert "frontend" not in got
    assert info["drift_vs_literal"]["dropped"] == ["frontend"]


@pytest.mark.parametrize(
    "payload, why",
    [
        ({"contexts": []}, "empty list -- protection off, or a scoped token"),
        ({"contexts": None}, "null contexts"),
        ({"strict": False}, "contexts key absent entirely"),
        ({}, "empty object"),
        (None, "the gh api call itself failed"),
    ],
)
def test_unusable_payload_never_yields_an_empty_required_set(payload, why):
    """THE LOAD-BEARING TEST. Each of these shapes is what a broken read looks
    like, and every one must fall back rather than become a verdict."""
    got, info = sha_green.classify_protection(payload)
    assert got, f"empty required set from {why!r} -- a gate that can only go green"
    assert got == FALLBACK
    assert info["source"] == "fallback_literal"
    assert info["trusted"] is False


def test_fallback_says_the_set_may_be_stale():
    """A fallback that does not announce itself is worse than no fallback: the
    caller cannot tell a measured answer from a remembered one (R5)."""
    _, info = sha_green.classify_protection(None)
    assert "UNMEASURED" in info["detail"]


def test_trusted_flag_distinguishes_measured_from_remembered():
    _, live = sha_green.classify_protection({"contexts": ["alpha"]})
    _, stale = sha_green.classify_protection(None)
    assert live["trusted"] is not stale["trusted"]


def test_module_default_is_the_fallback_until_resolved():
    """Import-time REQUIRED must be the literal, not an empty tuple -- a caller
    that forgets to call resolve_required() should get a conservative set,
    never a permissive one."""
    assert sha_green.REQUIRED == FALLBACK
    assert sha_green.REQUIRED
