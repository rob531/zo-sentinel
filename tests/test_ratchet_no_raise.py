"""A ratchet must be mechanically unable to raise its own number.

reachability_baseline.json records the near-miss these tests make impossible:

    "--update-baseline wanted to RAISE it 277 -> 335, which is the exact move
     this note forbids."

A human caught that one. Once ratchet-nightly.yml runs --update-baseline
unattended, nobody is watching, and a raise-capable updater would quietly
absorb every regression into the baseline -- turning the ratchet into a
thermometer that follows the tree downhill.

These tests pin BOTH directions: down must work, up must not.
"""
import importlib
import json

import pytest

MODULE = "tools.reachability_ratchet"


@pytest.fixture()
def rr(tmp_path, monkeypatch):
    import sys, os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    spec = importlib.util.spec_from_file_location(
        "reachability_ratchet", os.path.join(root, "tools", "reachability_ratchet.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    baseline = tmp_path / "baseline.json"
    monkeypatch.setattr(mod, "BASELINE_PATH", str(baseline), raising=False)
    return mod, baseline


def _seed(baseline, orphans=277, deferred=62, note="DOCTRINE NOTE -- keep me"):
    baseline.write_text(json.dumps({
        "orphan_count": orphans, "deferred_count": deferred, "note": note,
    }), encoding="utf-8")


def _read(baseline):
    return json.loads(baseline.read_text(encoding="utf-8"))


def test_refuses_to_raise(rr):
    """The founding case: 277 -> 335 must not happen."""
    mod, baseline = rr
    _seed(baseline)
    mod.write_baseline(335, "attempted raise", deferred_count=62)
    assert _read(baseline)["orphan_count"] == 277, (
        "a ratchet that can raise its own number is a thermometer"
    )


def test_allows_lowering(rr):
    mod, baseline = rr
    _seed(baseline)
    mod.write_baseline(200, "improvement", deferred_count=62)
    assert _read(baseline)["orphan_count"] == 200


def test_raise_requires_both_flag_and_reason(rr):
    mod, baseline = rr
    _seed(baseline)
    mod.write_baseline(335, "n", deferred_count=62, allow_raise=True, reason=None)
    assert _read(baseline)["orphan_count"] == 277, "allow_raise without a reason must not raise"
    mod.write_baseline(335, "n", deferred_count=62, allow_raise=True,
                       reason="CofC ruling 2026-xx-xx")
    assert _read(baseline)["orphan_count"] == 335, "an attributable raise is permitted"


def test_partial_refusal_still_banks_the_improvement(rr):
    """Orphans regressed but deferred improved: bank one, hold the other."""
    mod, baseline = rr
    _seed(baseline, orphans=277, deferred=62)
    mod.write_baseline(335, "mixed", deferred_count=40)
    doc = _read(baseline)
    assert doc["orphan_count"] == 277      # held
    assert doc["deferred_count"] == 40     # banked


def test_doctrine_note_is_preserved(rr):
    """The note carries the CofC ruling; a changelog must not replace it."""
    mod, baseline = rr
    _seed(baseline, note="DOCTRINE NOTE -- keep me")
    mod.write_baseline(200, "ratchet updated by --update-baseline", deferred_count=62)
    assert "DOCTRINE NOTE" in _read(baseline)["note"]


def test_change_is_recorded_in_history(rr):
    mod, baseline = rr
    _seed(baseline)
    mod.write_baseline(200, "improvement", deferred_count=62, reason="mounted 77 routers")
    hist = _read(baseline).get("history")
    assert hist and hist[-1]["changes"]["orphan_count"] == [277, 200]
    assert "mounted 77 routers" in hist[-1]["reason"]


def test_missing_baseline_pins_without_complaint(rr):
    """First run has nothing to compare against; it must pin, not refuse."""
    mod, baseline = rr
    mod.write_baseline(335, "initial", deferred_count=62)
    assert _read(baseline)["orphan_count"] == 335
