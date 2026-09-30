"""The evaluator's pytest target set must be CONSTRUCTED, never hand-listed.

Context (cycle-0162, FU-572): `.github/workflows/evaluator.yml` named 74
`tests/test_*.py` files by hand while 192 sat on disk. `pytest` is a REQUIRED
check, so the other 118 gated nothing -- and cycles 0158/0159/0160 each shipped
a negative-control suite that this check never executed, then published the
resulting green as evidence. The 2026-09-01 note in that workflow records the
same class; it was cured then by adding six filenames by hand, which is exactly
why it came back.

This file is the latch. It asserts, inside the very check it is about:

  1. the workflow invokes tools/ci_pytest.py, and hand-lists NO test path
  2. every quarantine entry names a file that exists (no silent widening)
  3. every quarantine entry carries a reason
  4. targets and quarantine partition the on-disk set exactly
  5. a file that appears on disk is collected with NO list edit anywhere
  6. an empty target set exits 2, never 0 -- "collected nothing" is not a pass

Every one of those carries its own MUTANT: the same checker is fed a
deliberately broken input and must reject it. An assertion never observed red
is not evidence (R4), so the red is produced here on every run rather than
claimed once in a PR body.

Stdlib only, by design: this must run wherever the evaluator runs.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "evaluator.yml")
CONSTRUCTOR = os.path.join(ROOT, "tools", "ci_pytest.py")
QUARANTINE = os.path.join(ROOT, "tests", "ci", "pytest_quarantine.txt")

sys.path.insert(0, os.path.join(ROOT, "tools"))
import ci_pytest  # noqa: E402


# --------------------------------------------------------------------------
# helpers under test -- shared by the real assertion and by its mutant, so a
# control can never drift away from the check it is supposed to falsify.
# --------------------------------------------------------------------------

def _strip_comments(text: str) -> str:
    """Drop YAML comments before scanning for invocations.

    FU-305: a commented-out path string reads as an invocation to a raw-text
    scan. The first draft of the 2026-09-30 evaluator edit was bitten by the
    inverse of this (a comment between two backslash-continued lines silently
    terminated the command), so comment handling here is deliberate, not
    incidental.
    """
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out.append(line.split(" #", 1)[0])
    return "\n".join(out)


def hand_listed_targets(workflow_text: str) -> list[str]:
    """Test paths named as pytest arguments in the workflow (comments removed)."""
    import re

    body = _strip_comments(workflow_text)
    return re.findall(r"(?<![\w/])tests/test_[A-Za-z0-9_]+\.py", body)


def invokes_constructor(workflow_text: str) -> bool:
    return "tools/ci_pytest.py" in _strip_comments(workflow_text)


# --------------------------------------------------------------------------
# 1. the workflow constructs its target set
# --------------------------------------------------------------------------

def test_workflow_invokes_the_constructor():
    text = open(WORKFLOW, encoding="utf-8").read()
    assert invokes_constructor(text), (
        "evaluator.yml must run tools/ci_pytest.py; without it the target set "
        "is whatever someone last remembered to type."
    )


def test_workflow_hand_lists_no_test_file():
    text = open(WORKFLOW, encoding="utf-8").read()
    listed = hand_listed_targets(text)
    assert listed == [], (
        "evaluator.yml hand-lists %d test path(s): %s. A hand list is the defect "
        "FU-572 records -- add the file to tests/, or exclude it in "
        "tests/ci/pytest_quarantine.txt with a reason."
        % (len(listed), sorted(set(listed))[:5])
    )


def test_MUTANT_a_hand_listed_path_is_detected():
    """NEGATIVE CONTROL for the two checks above -- both observed RED."""
    mutant = textwrap.dedent(
        """
        jobs:
          pytest:
            steps:
              - run: |
                  python -m pytest \\
                    tests/test_scoring.py \\
                    -q
        """
    )
    assert hand_listed_targets(mutant) == ["tests/test_scoring.py"]
    assert not invokes_constructor(mutant)


def test_MUTANT_a_commented_out_path_is_not_read_as_an_invocation():
    """FU-305's class, controlled: a comment must not count as a hand list."""
    commented = textwrap.dedent(
        """
        jobs:
          pytest:
            steps:
              # tests/test_scoring.py -- retired 2026-01-01
              - run: python tools/ci_pytest.py -q   # tests/test_other.py
        """
    )
    assert hand_listed_targets(commented) == []
    assert invokes_constructor(commented)


# --------------------------------------------------------------------------
# 2 + 3. the quarantine file is honest
# --------------------------------------------------------------------------

def test_no_stale_quarantine_entry():
    r = ci_pytest.resolve(ROOT, QUARANTINE)
    assert r["stale"] == [], (
        "%d quarantine entry(ies) name a file that does not exist: %s. A renamed "
        "or deleted test leaves the exclusion behind, which widens it silently."
        % (len(r["stale"]), r["stale"])
    )


def test_every_quarantine_entry_states_a_reason():
    _, reasons = ci_pytest.read_quarantine(QUARANTINE)
    bare = sorted(p for p, why in reasons.items() if not why)
    assert bare == [], (
        "%d quarantine entry(ies) carry no reason: %s. An unexplained exclusion "
        "is a graveyard entry -- it will never be revisited." % (len(bare), bare[:5])
    )


def test_MUTANT_a_stale_entry_and_a_bare_entry_are_both_caught(tmp_path):
    """NEGATIVE CONTROL for the two checks above -- both observed RED."""
    root = tmp_path / "repo"
    (root / "tests" / "ci").mkdir(parents=True)
    (root / "tests" / "test_real.py").write_text("def test_x():\n    pass\n")
    q = root / "tests" / "ci" / "pytest_quarantine.txt"
    q.write_text(
        "tests/test_real.py  # a stated reason\n"
        "tests/test_vanished.py  # names a file that is not there\n"
        "tests/test_bare.py\n"
    )
    r = ci_pytest.resolve(str(root), str(q))
    assert sorted(r["stale"]) == ["tests/test_bare.py", "tests/test_vanished.py"]
    _, reasons = ci_pytest.read_quarantine(str(q))
    assert [p for p, why in reasons.items() if not why] == ["tests/test_bare.py"]


# --------------------------------------------------------------------------
# 4 + 5. the partition holds, and a new file needs no list edit
# --------------------------------------------------------------------------

def test_targets_and_quarantine_partition_the_on_disk_set():
    r = ci_pytest.resolve(ROOT, QUARANTINE)
    disk = set(r["disk"])
    targets = set(r["targets"])
    quarantined = set(r["quarantined"]) & disk
    assert targets & quarantined == set(), "a file is both collected and excluded"
    assert targets | quarantined == disk, (
        "on-disk tests unaccounted for: %s"
        % sorted(disk - (targets | quarantined))[:5]
    )
    assert targets, "the resolved target set is empty; that is never a green"


def test_a_new_test_file_is_collected_with_no_list_edit(tmp_path):
    """THE defect this cycle cures, asserted directly.

    Drop a brand-new test file into a tree and resolve again. It must appear in
    the target set without a single edit to the workflow or the quarantine --
    that is the whole difference from the hand list, so it is asserted, not
    described.
    """
    root = tmp_path / "repo"
    (root / "tests" / "ci").mkdir(parents=True)
    q = root / "tests" / "ci" / "pytest_quarantine.txt"
    q.write_text("# nothing excluded\n")
    (root / "tests" / "test_existing.py").write_text("def test_a():\n    pass\n")

    before = ci_pytest.resolve(str(root), str(q))["targets"]
    assert before == ["tests/test_existing.py"]

    (root / "tests" / "test_brand_new.py").write_text("def test_b():\n    pass\n")
    after = ci_pytest.resolve(str(root), str(q))["targets"]

    assert "tests/test_brand_new.py" in after, (
        "a new test file did not enter the target set -- the hand-list defect "
        "would still be live"
    )
    assert len(after) == len(before) + 1


# --------------------------------------------------------------------------
# 6. collecting nothing is not a pass
# --------------------------------------------------------------------------

def test_an_empty_target_set_exits_2_not_0(tmp_path):
    """NEGATIVE CONTROL, run as a real subprocess: rc is observed, not reasoned."""
    q = tmp_path / "everything.txt"
    disk = ci_pytest.on_disk(ROOT)
    assert disk, "no tests on disk at all -- the fixture is meaningless"
    q.write_text("".join("%s  # excluded by the control\n" % p for p in disk))

    proc = subprocess.run(
        [sys.executable, CONSTRUCTOR, "--quarantine", str(q)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 2, (
        "an empty target set must exit 2 (REFUSED), got rc=%d. rc=0 there would "
        "let a run that collected nothing report as a pass.\nstderr: %s"
        % (proc.returncode, proc.stderr[-2000:])
    )
    assert "EMPTY" in (proc.stdout + proc.stderr)


def test_a_missing_quarantine_file_is_refused_not_ignored(tmp_path):
    """R6: unknown is not zero. No exclusion file must not read as 'exclude nothing'."""
    proc = subprocess.run(
        [sys.executable, CONSTRUCTOR, "--quarantine", str(tmp_path / "nope.txt"), "--census"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2, (
        "a missing quarantine file must exit 2, got rc=%d" % proc.returncode
    )


def test_census_is_side_effect_free_and_machine_readable():
    proc = subprocess.run(
        [sys.executable, CONSTRUCTOR, "--census"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    import json

    data = json.loads(proc.stdout)
    assert data["on_disk"] == data["targets"] + len(
        [p for p in ci_pytest.resolve(ROOT, QUARANTINE)["quarantined"]
         if p in set(ci_pytest.on_disk(ROOT))]
    )
    assert data["stale"] == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
