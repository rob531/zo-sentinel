"""The LEVEL trigger of the reachability ratchet, OBSERVED RED -- issue #3944.

Issue #3944 has been open since 2026-08-25 on one question: should the over-cap
DEFERRED LEVEL block the builder queue? The 2026-09-21 chairman ruling rejected both
options on the table and named three preconditions for the one that survives, of which
the third is binding:

    "A fixture that OBSERVES the trigger exit non-zero. ... A check never seen RED is
     unproven, and this one has been unproven for 26 days. Arming an unobserved trigger
     on the builder queue would take the exact shape this issue was opened to complain
     about -- an instrument asserted rather than measured -- and point it at the thing
     that produces all our work."

and it is explicit about scope:

    "What the fleet does absent a ruling: completes the re-derivation on the fixed
     counter and builds the RED-OBSERVING FIXTURE, and stops there. It will not arm the
     trigger, will not raise or lower the cap, will not edit the baseline, and will not
     retire this issue."

This file is that fixture, and nothing more. It changes no policy. Four tests, and the
last three exist so the first one means something:

  1. ARMED + over cap        -> rc != 0   THE RED OBSERVATION. Precondition 3.
  2. NOT armed + over cap    -> rc == 0   nothing has been armed; this is the state of
                                          every real run today, and it is the negative
                                          control for test 1: the flag is what changes
                                          the verdict, not the census.
  3. ARMED + under cap       -> rc == 0   the flag does not simply always fail. A
                                          trigger that fires on every input measures
                                          nothing, which is the mirror of the defect
                                          #3944 reports.
  4. the CI workflows        -> the flag is ABSENT from .github/workflows/**. The
                                          "not armed" promise is pinned to THE ARTIFACT
                                          THAT RUNS (R1/R2), not to a sentence in a
                                          docstring. Arming the level later has to break
                                          this test by name, deliberately, with the
                                          policy call made in #3944 first.

The census is stubbed so every other failure branch is quiet: the verdict below turns on
the level rule alone, and a change to some unrelated integrity check cannot silently make
test 1 pass for the wrong reason.
"""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def rr(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "reachability_ratchet_level",
        os.path.join(ROOT, "tools", "reachability_ratchet.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    baseline = tmp_path / "baseline.json"
    monkeypatch.setattr(mod, "BASELINE_PATH", str(baseline), raising=False)
    monkeypatch.setattr(mod, "ARTIFACT_DIR", str(tmp_path / "artifacts"), raising=False)
    monkeypatch.setattr(mod, "ARTIFACT_PATH",
                        str(tmp_path / "artifacts" / "r.json"), raising=False)
    return mod, baseline


def _stub(mod, monkeypatch, baseline, deferred_n):
    """A census whose ONLY interesting property is the size of the deferred list.

    orphan_count is set so that `effective` lands exactly ON the baseline: not above
    (which would append "undeclared new orphans") and not below (which only prints).
    Every integrity list is empty, so "stale deferrals", "reasonless deferrals" and
    "reasonless exemptions" cannot fire. The derivative rule is fed refs equal to the
    current count, so DEFERRED NON-INCREASING holds. What is left is the level.
    """
    base_orphans = 277
    deferred = ["mod_%d" % i for i in range(deferred_n)]
    baseline.write_text(json.dumps({
        "orphan_count": base_orphans, "deferred_count": deferred_n,
        "note": "fixture",
    }), encoding="utf-8")
    monkeypatch.setattr(mod, "census", lambda: {
        "router_modules_total": base_orphans + deferred_n + 32,
        "mounted_count": 32,
        "exempted_count": 0,
        "orphan_count": base_orphans + deferred_n,
        "orphans": [],
        "deferred_active": deferred,
        "deferred_stale": [],
        "deferred_reasonless": [],
        "exempt_reasonless": [],
    }, raising=False)
    # HEAD~1 is not a meaningful reference inside a tmp fixture; the baseline file
    # carries the comparison, exactly as it does in shallow CI.
    monkeypatch.setattr(mod, "deferred_count_at", lambda ref: None, raising=False)


def _run(mod, monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["reachability_ratchet.py"] + argv)
    return mod.main()


OVER = 63          # the level this issue was filed about
UNDER = 39         # below the cap of 40


def test_level_trigger_fires_red_when_armed(rr, monkeypatch, capsys):
    """PRECONDITION 3. The trigger is OBSERVED exiting non-zero."""
    mod, baseline = rr
    assert OVER > mod.DEFERRED_REVIEW_CAP
    _stub(mod, monkeypatch, baseline, OVER)
    rc = _run(mod, monkeypatch, ["--enforce", "--enforce-level", "--quiet"])
    out = capsys.readouterr().out
    assert rc != 0, "the level trigger did not fire: it is still unproven"
    assert "deferred level over cap (63 > 40)" in out, (
        "it failed, but not for the level -- a red for the wrong reason proves nothing")


def test_level_trigger_is_not_armed_by_default(rr, monkeypatch, capsys):
    """THE NEGATIVE CONTROL for the test above, and the state of every real run.

    The SAME census that goes red above must exit 0 here. That is what makes test 1 a
    measurement of the flag rather than of the fixture -- and it is the standing proof
    that building the fixture did not arm anything.
    """
    mod, baseline = rr
    _stub(mod, monkeypatch, baseline, OVER)
    rc = _run(mod, monkeypatch, ["--enforce", "--quiet"])
    out = capsys.readouterr().out
    assert rc == 0, "the level is ARMED without --enforce-level -- #3944 is not decided"
    assert "DEFERRED LEVEL OVER CAP" in out, (
        "the advisory line vanished; the level must still be REPORTED while unarmed")


def test_level_trigger_does_not_fire_under_cap(rr, monkeypatch):
    """A trigger that fires on every input measures nothing -- the mirror of #3944."""
    mod, baseline = rr
    assert UNDER < mod.DEFERRED_REVIEW_CAP
    _stub(mod, monkeypatch, baseline, UNDER)
    assert _run(mod, monkeypatch, ["--enforce", "--enforce-level", "--quiet"]) == 0


def test_no_workflow_arms_the_level():
    """R1/R2: the promise is pinned to THE ARTIFACT THAT RUNS, not to a docstring.

    #3944 is open precisely because what a check SAYS about itself drifted from what it
    DOES. So "not armed" is asserted against the workflow files themselves. Arming the
    level has to break this test, by name, on purpose -- after the policy call is made
    in #3944.
    """
    wf = os.path.join(ROOT, ".github", "workflows")
    assert os.path.isdir(wf), "workflows dir not found -- this test must not skip"
    armed = []
    for dirpath, _dirs, files in os.walk(wf):
        for f in files:
            if not f.endswith((".yml", ".yaml")):
                continue
            p = os.path.join(dirpath, f)
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                if "--enforce-level" in fh.read():
                    armed.append(os.path.relpath(p, ROOT))
    assert armed == [], (
        "--enforce-level appears in %s. That ARMS the deferred LEVEL against the "
        "builder queue, which is the open policy question in issue #3944 and is not a "
        "lane's call to make." % armed)
