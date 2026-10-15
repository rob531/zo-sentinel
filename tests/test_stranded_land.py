"""#4079 / cycle-0197: pin tools/stranded_land.py's self-test in CI.

The lander carried self-test poles since cycle-0177 but NOTHING ran them in
pytest -- only the grader (test_stranded_review.py) had a wrapper. cycle-0197
added a second pre-push gate (referent_verify columns) with poles P12..P12d;
this makes CI execute all of them, so a regression of the gate set is caught by
the same pytest that gates every PR."""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "stranded_land.py"


def test_stranded_land_self_test_passes():
    r = subprocess.run([sys.executable, str(TOOL), "--self-test"],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 failing" in r.stdout, r.stdout
