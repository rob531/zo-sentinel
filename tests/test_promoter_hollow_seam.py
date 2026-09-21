"""FU-236 seam 4 -- the promoter's own enumeration.

WHY THIS TEST EXISTS. On 2026-08-03 the hollow rule was armed at three seams:
goose_runner (pre-.done), the publisher (pre-PR) and tests/ci/no_hollow_scaffold.py
(pre-merge). All three fire when a file becomes a COMMIT. `promote_staged_to_active.py`
enumerates services/staged/ on DISK, so on 2026-08-04 it admitted 2 hollow members
into the PROMOTE cohort -- 7 of the 12 hollow files on disk were UNTRACKED, had never
been a PR, and therefore no armed seam had ever looked at them.

R4: an assertion never observed RED is UNPROVEN. This test is the two-point control
the rest of this repo is held to -- the predicate must be seen REJECTING a known-hollow
member AND ACCEPTING a known-substantive one. A predicate that reds everything is a
rubber stamp with the sign flipped.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from zo_sentinel.gates.hollow import hollow_service_member_scan


def _scan(sdir, name):
    hits = []
    for dp, _dd, files in os.walk(sdir):
        if "__pycache__" in dp:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            a = os.path.join(dp, fn)
            rel = "services/staged/%s/%s" % (
                name, os.path.relpath(a, sdir).replace(os.sep, "/"))
            with open(a, encoding="utf-8", errors="replace") as fh:
                why = hollow_service_member_scan(rel, fh.read())
            if why:
                hits.append(rel)
    return hits


def test_seam_rejects_comment_only_contract(tmp_path):
    """The exact 33-byte shape that entered the PROMOTE cohort on 2026-08-04."""
    d = tmp_path / "verdict_health_clone"
    d.mkdir()
    (d / "contract.py").write_text("# Let me check the exemplar first\n")
    assert _scan(str(d), "verdict_health_clone"), (
        "predicate did not reject a comment-only contract.py -- this is the file "
        "that made contract_ok=True mean nothing")


def test_seam_rejects_zero_byte_member(tmp_path):
    """0-byte router.py -- observed in the ACTIVE lane on 2026-08-04."""
    d = tmp_path / "zero_byte_svc"
    d.mkdir()
    (d / "router.py").write_text("")
    assert _scan(str(d), "zero_byte_svc"), "predicate did not reject a 0-byte member"


def test_seam_accepts_substantive_service(tmp_path):
    """NEGATIVE CONTROL. Without this the suite cannot tell a gate from a wall."""
    d = tmp_path / "substantive_svc"
    d.mkdir()
    (d / "contract.py").write_text(
        "from app.models import McpServerRegistry\n"
        "def check(session):\n"
        "    rows = session.query(McpServerRegistry).all()\n"
        "    assert rows is not None, 'registry unreadable'\n"
        "    return len(rows)\n")
    (d / "router.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/x')\n"
        "def x():\n"
        "    return {'ok': True}\n")
    assert _scan(str(d), "substantive_svc") == [], (
        "predicate rejected a substantive service -- it is not discriminating, "
        "it is refusing everything")


def test_promoter_imports_the_same_rule_object():
    """Seam 4 must IMPORT the rule, never re-implement it, or the predicate can
    drift between the commit path and the file path."""
    src = open(os.path.join(ROOT, "tools", "promote_staged_to_active.py"),
               encoding="utf-8").read()
    assert "from zo_sentinel.gates.hollow import hollow_service_member_scan" in src
    assert "hollow member(s):" in src, "seam 4 contributes no reason to the HOLD list"
