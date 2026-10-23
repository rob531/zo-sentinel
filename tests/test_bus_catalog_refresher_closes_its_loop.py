"""The catalog refresher's two success signals, driven to RED and to GREEN.

WHY THIS FILE EXISTS
  Measured on the ZoComputer host 2026-09-26: 169 consecutive hourly runs of
  tools/bus_catalog_guard.sh wrote `refresh_exit_code: 0` to
  /home/workspace/logs/bus_catalog_heartbeat.json while
  origin/main:schema/bus_catalog.json stayed frozen at 2026-09-19T11:07:06Z --
  7.06d old, past referent-verify's 7d WARN, and 6.9d from the 14d budget at
  which that required check turns STALE-RED and blocks every pull request on
  this repository, including the one that would have fixed it.

  Nothing was broken. tools/bus_catalog_refresh.sh pushed the branch, opened the
  PR, and exited 0 -- and nothing merged the PR. The script's own comment block
  had worked out that "a pushed branch is not a refreshed snapshot; the push was
  never the arming, the merge is", and then ended one notch short of the same
  mistake. The heartbeat, built expressly so a dead refresher would show as a
  field that stopped moving, recorded the refresh's opinion of itself instead of
  a measurement of main, so it could not tell "refreshed" from "opened a PR
  nobody will merge".

  Both halves now carry a signal that can go red, and rule 3 / R4 say an
  assertion never observed RED is not evidence. So each one is driven to both
  poles here, against the text of the shipped scripts rather than a copy of it.
"""
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "tools" / "bus_catalog_guard.sh"
REFRESH = ROOT / "tools" / "bus_catalog_refresh.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="drives bash; runs on the Linux CI runner"
)


def _heartbeat_writer() -> str:
    """The heartbeat's python heredoc, lifted from the shipped guard script.

    Extracted rather than copied: if the writer is renamed or rewritten this
    returns something different (or raises), so the test follows the artifact
    that runs instead of asserting against a stale duplicate of it.
    """
    text = GUARD.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'\n(.*?)\nPY\n", text, re.S)
    for b in blocks:
        if "snapshot_advanced" in b:
            return b
    raise AssertionError(
        "no heartbeat writer emitting snapshot_advanced found in "
        f"{GUARD.name}; the refresher's landed signal is gone"
    )


def _run_heartbeat(tmp_path, before, after) -> dict:
    src = tmp_path / "hb.py"
    src.write_text(_heartbeat_writer(), encoding="utf-8")
    out = tmp_path / "hb.json"
    subprocess.run(
        [sys.executable, str(src), str(out), str(before), "1", "test", "0", str(after)],
        check=True,
    )
    return json.loads(out.read_text(encoding="utf-8"))


def test_heartbeat_reports_the_169_hour_lie_as_not_advanced(tmp_path):
    """RED POLE. The exact measurements the host saw for seven days.

    before == after == 169.0 with a refresh that exited 0. The old writer
    recorded only `refresh_exit_code: 0` and was indistinguishable from a
    healthy run. The new one must call this what it is.
    """
    hb = _run_heartbeat(tmp_path, 169.0, 169.0)
    assert hb["snapshot_advanced"] is False, (
        "a snapshot that did not move by one second in 169h was reported as "
        "advanced -- this is the defect, not a regression of it"
    )
    assert hb["refresh_exit_code"] == 0, (
        "the exit code must still be recorded; the point is that it is no "
        "longer the only signal, not that it is suppressed"
    )


def test_heartbeat_reports_a_real_refresh_as_advanced(tmp_path):
    """GREEN POLE. 169.0h -> 0.43h, the measurement taken after #5575 merged."""
    hb = _run_heartbeat(tmp_path, 169.0, 0.43)
    assert hb["snapshot_advanced"] is True
    assert hb["snapshot_age_hours_after"] == pytest.approx(0.43)


def test_heartbeat_treats_an_unreadable_snapshot_as_unknown_not_advanced(tmp_path):
    """RED POLE, R6. age_hours() returns -1 for missing or unparseable.

    -1 is smaller than any real `before`, so an arithmetic comparison alone
    would read a VANISHED snapshot as the freshest one ever measured.
    """
    hb = _run_heartbeat(tmp_path, 169.0, -1.0)
    assert hb["snapshot_advanced"] is False, (
        "unknown is not zero and is not an advance (R6)"
    )


def _finisher() -> str:
    """The finish_with_automerge() definition, lifted from the shipped script."""
    text = REFRESH.read_text(encoding="utf-8")
    m = re.search(r"^finish_with_automerge\(\) \{\n.*?^\}\n", text, re.S | re.M)
    assert m, (
        f"finish_with_automerge() not found in {REFRESH.name}; the refresher "
        "no longer arms the merge, so nothing lands its snapshot"
    )
    return m.group(0)


def _run_finisher(tmp_path, gh_rc, snapshot_json):
    """Drive the finisher with gh and git stubbed, and return (rc, log)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)  # one test drives both poles in one tmp_path
    (bin_dir / "gh").write_text(f"#!/bin/sh\nexit {gh_rc}\n", encoding="utf-8")
    # `git show origin/main:<SNAP>` is the only honest oracle for "did it land"
    # (R1), so the stub is what stands in for origin/main here.
    (bin_dir / "git").write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f"  *show*) printf '%s' '{snapshot_json}' ;;\n"
        "  *) : ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    for p in bin_dir.iterdir():
        p.chmod(0o755)

    script = tmp_path / "harness.sh"
    script.write_text(
        "set -uo pipefail\n"
        'log() { echo "[catalog-refresh] $*"; }\n'
        'SNAP="schema/bus_catalog.json"\n'
        + _finisher()
        + "\nfinish_with_automerge 4242\n",
        encoding="utf-8",
    )
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    r = subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True
    )
    return r.returncode, r.stdout


FRESH = '{"captured_at": "2099-01-01T00:00:00+00:00"}'


def test_an_unmergeable_pr_exits_nonzero(tmp_path):
    """RED POLE, and the one that matters.

    gh refuses to arm auto-merge, so the branch is pushed, the PR is open, and
    nothing on earth will land it. That is precisely the state the host sat in
    for 169 hours reporting rc=0. It must now be non-zero, because the guard
    only writes a visible `refresh_exit_code != 0` if this returns one.
    """
    rc, log = _run_finisher(tmp_path, gh_rc=1, snapshot_json=FRESH)
    assert rc == 5, f"an unmergeable PR still reported success (rc={rc}): {log}"
    assert "automerge_failed=#4242" in log


def test_an_armed_pr_exits_zero(tmp_path):
    """GREEN POLE. Without this, rc=5 above could be an unconditional failure.

    An assertion that cannot be observed going green measures nothing either --
    the negative control has two poles, not one.
    """
    rc, log = _run_finisher(tmp_path, gh_rc=0, snapshot_json=FRESH)
    assert rc == 0, f"an armed PR was reported as a failure (rc={rc}): {log}"
    assert "automerge_armed=#4242" in log


def test_the_landed_age_is_read_from_main_and_unknown_stays_unknown(tmp_path):
    """The verdict is resolved from origin/main, never from gh's output (R1).

    An empty read is -1, not 0. A stale-but-true catalog is recoverable; a
    missing one reported as 0.00h old is the `tool_count` zero-fill defect (R6)
    wearing the refresher's clothes.
    """
    _, log = _run_finisher(tmp_path, gh_rc=0, snapshot_json=FRESH)
    assert "landed_age_hours=" in log
    assert "landed_age_hours=-1" not in log, "a readable snapshot reported UNKNOWN"

    _, log_missing = _run_finisher(tmp_path, gh_rc=0, snapshot_json="")
    assert "landed_age_hours=-1" in log_missing, (
        "a snapshot absent from main did not report UNKNOWN: " + log_missing
    )
