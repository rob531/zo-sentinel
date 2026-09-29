"""R3 control for the DEFERRED LEVEL line (cycle-0156, 2026-09-29).

On 2026-09-29 the deferred list fell 51 -> 36 against a cap of 40 and the
"DEFERRED LEVEL OVER CAP" line stopped printing. That is the exact reading
HARNESS_DOCTRINE R3 forbids taking on trust: *a bucket that went to ZERO must
prove the check RAN*. "The level fell" and "the branch was deleted" produce an
identical log -- silence.

So the branch is pinned at both poles here. If someone removes the branch to
quieten it, the OVER pole goes red. Nothing in this file changes a threshold,
a branch or an exit code; deferred_level_line() is pure.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RATCHET = os.path.join(ROOT, "tools", "reachability_ratchet.py")


def _load():
    spec = importlib.util.spec_from_file_location("reachability_ratchet", RATCHET)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["reachability_ratchet"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_over_cap_pole_still_fires():
    """THE NEGATIVE CONTROL. One over the cap must still produce the line."""
    m = _load()
    text, over = m.deferred_level_line(m.DEFERRED_REVIEW_CAP + 1)
    assert over is True
    assert text is not None
    assert "DEFERRED LEVEL OVER CAP" in text
    assert str(m.DEFERRED_REVIEW_CAP + 1) in text
    assert str(m.DEFERRED_REVIEW_CAP) in text


def test_at_and_under_cap_are_silent():
    m = _load()
    for n in (0, 1, m.DEFERRED_REVIEW_CAP - 1, m.DEFERRED_REVIEW_CAP):
        text, over = m.deferred_level_line(n)
        assert over is False, n
        assert text is None, n


def test_the_51_to_36_transition_is_the_level_not_the_branch():
    """The actual cycle-0156 numbers, pinned as a pair.

    51 printed; 36 does not. Both readings come from the SAME callable, so a
    later run seeing silence at 36 can prove the branch is intact by seeing
    the 51 pole still speak.
    """
    m = _load()
    assert m.deferred_level_line(51)[1] is True
    assert m.deferred_level_line(36)[1] is False


def test_cap_is_not_quietly_raised():
    """The 2026-07-21 CofC ruling: the cap is not raised to quieten the line."""
    m = _load()
    assert m.DEFERRED_REVIEW_CAP == 40


def test_main_still_routes_the_level_through_the_helper():
    """Guards the extraction itself: main must not re-inline the text."""
    src = open(RATCHET, encoding="utf-8").read()
    assert "deferred_level_line(len(active_deferred))" in src
    assert src.count("DEFERRED LEVEL OVER CAP") == 1, (
        "the line must exist in exactly one place -- the helper")
