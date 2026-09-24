"""cycle-0062 -- the promoter names the model-name-drift cure, and does not lie.

`tools/repair_staged_model_names.py` repairs the family that holds the most staged
services. It was built 2026-08-06 and consulted by NOTHING for 27 days: the answer to
"which of these HOLDs is one mechanical rename away from promotable" existed on disk
the whole time with no reader on the surface where the HOLD is produced.

Three properties, each with the control that was OBSERVED RED before the wiring landed
(recorded in FOLLOWUPS.md, cycle-0062):

  1. WIRED       -- the promoter actually imports the repair module. The control is
                    the pre-change state: `dark_tools.py --assert-wired
                    tools/repair_staged_model_names.py` exited 1 (DARK).
  2. R6          -- an unavailable summary reports UNKNOWN and emits NO counts. A
                    `family_a_repairable_sites: 0` from a module that never ran reads
                    as "nothing to repair" and is the inversion this repo keeps paying
                    for. Control: test_unavailable_emits_no_counts asserts the count
                    keys are ABSENT, and fails if the code ever zero-fills them.
  3. NON-MUTATING -- the summary is a DRY RUN. A tool that repairs in order to measure
                    throws the repair away and misreports the state it measured
                    (measured 2026-08: one such tool silently repaired 67 files).
                    Control: the staged tree is hashed before and after.
"""
import hashlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

promoter = pytest.importorskip("tools.promote_staged_to_active")

COUNT_KEYS = (
    "family_a_repairable_sites",
    "family_a_services",
    "family_b_unmapped_sites",
    "family_b_services",
    "family_b_distinct",
    "staged_services",
)


def _staged_digest():
    """Content hash of every .py under services/staged. Order-stable."""
    staged = os.path.join(ROOT, "services", "staged")
    h = hashlib.sha256()
    if not os.path.isdir(staged):
        return None
    for root, dirs, files in os.walk(staged):
        dirs.sort()
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            p = os.path.join(root, f)
            h.update(os.path.relpath(p, staged).replace(os.sep, "/").encode())
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


def test_repair_module_is_actually_imported():
    """1. WIRED. Not "a file exists" -- the promoter holds a live reference to it."""
    assert hasattr(promoter, "_repair"), "promoter does not carry the repair module at all"
    assert promoter._repair is not None, "repair module present but did not import"
    assert callable(getattr(promoter._repair, "run", None))
    assert callable(promoter.model_name_repair_summary)


def test_unavailable_emits_no_counts(monkeypatch):
    """2. R6 -- UNKNOWN IS NOT ZERO.

    This is the negative control. With the repair module removed, the summary must
    say so. If the implementation is ever "simplified" to return zeros on failure,
    this test goes RED -- which is the whole point of it existing.
    """
    monkeypatch.setattr(promoter, "_repair", None)
    out = promoter.model_name_repair_summary()
    assert out["status"] == "unavailable"
    assert out.get("detail")
    for k in COUNT_KEYS:
        assert k not in out, (
            "R6 violation: %r was emitted for a module that never ran; an unmeasured "
            "zero is indistinguishable from a measured one" % k)


def test_raising_repair_is_reported_not_swallowed(monkeypatch):
    """2b. A crash inside the cure must surface as UNKNOWN, never as a clean zero."""
    class Boom:
        @staticmethod
        def run(*_a, **_kw):
            raise RuntimeError("synthetic")

    monkeypatch.setattr(promoter, "_repair", Boom)
    out = promoter.model_name_repair_summary()
    assert out["status"] == "unavailable"
    assert "synthetic" in out["detail"]
    for k in COUNT_KEYS:
        assert k not in out


def test_summary_is_a_dry_run_and_mutates_nothing():
    """3. NON-MUTATING. The measurement must not be the repair."""
    before = _staged_digest()
    if before is None:
        pytest.skip("services/staged not present in this checkout")
    out = promoter.model_name_repair_summary()
    after = _staged_digest()
    assert after == before, (
        "model_name_repair_summary() MUTATED services/staged -- a tool that repairs "
        "in order to measure throws the repair away")
    assert out["status"] in ("measured", "unavailable")
    if out["status"] == "measured":
        for k in COUNT_KEYS:
            assert isinstance(out[k], int), "%s must be an int, got %r" % (k, out[k])
        assert out["cure"].startswith("python tools/repair_staged_model_names.py")


def test_report_carries_the_field_in_observe_mode(monkeypatch, tmp_path):
    """The field must reach the ARTIFACT, not just the function.

    A function nobody serialises is the same object as a tool nobody calls, which is
    the defect this whole cycle is about -- so the assertion is on the written JSON.

    `scan()` is stubbed to empty and the summary to a fixed dict so this runs in
    milliseconds and, more importantly, so the test can never itself walk 1100 staged
    services or move one. The artifact path is redirected into tmp_path: a test that
    overwrites the real artifacts/staged_promotion_report.json would hand the next
    reader a report generated by a test run.
    """
    import json

    artifact = tmp_path / "staged_promotion_report.json"
    monkeypatch.setattr(promoter, "ARTIFACT", str(artifact))
    monkeypatch.setattr(promoter, "scan", lambda: [])
    monkeypatch.setattr(promoter, "model_name_repair_summary",
                        lambda *a, **k: {"status": "measured",
                                         "family_a_repairable_sites": 7,
                                         "cure": "python tools/repair_staged_model_names.py --apply"})
    rc = promoter.main(["--quiet"])
    assert rc == 0
    rep = json.loads(artifact.read_text(encoding="utf-8"))
    assert rep["mode"] == "observe"
    assert rep["promoted"] == []
    assert "model_name_repair" in rep, "the wiring never reached the report"
    assert rep["model_name_repair"]["family_a_repairable_sites"] == 7
