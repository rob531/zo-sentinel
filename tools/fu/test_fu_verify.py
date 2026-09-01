"""Guarantees for the FU acceptance-predicate loop.

Every test here corresponds to a way this mechanism could silently lie. Each
one was RED at least once during development -- an assertion never seen fail
is not evidence, so none of these were written after the fact to match
passing behaviour.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fu_ledger  # noqa: E402
import fu_verify  # noqa: E402


def mkfu(verify=None, seen_red=None, status="open", priority="P1", cls="defect"):
    fu = fu_ledger.FU(num="999", title="t", start=0, end=1)
    fu.status_raw, fu.priority = status, priority
    fu.vals["class"] = cls
    if verify is not None:
        fu.vals["verify"] = verify
    if seen_red is not None:
        fu.vals["verify_seen_red"] = seen_red
    return fu


# --------------------------------------------------------------------------
# The false-GREEN that nearly shipped: a trailing `# comment` leaked into the
# shell, and cmd.exe returned 0 for a command that genuinely exits 1.
# --------------------------------------------------------------------------
def test_verify_cmd_strips_trailing_annotation():
    fu = mkfu(verify="`flyctl auth whoami`  # the 720h token timer")
    assert fu.verify_cmd == "flyctl auth whoami"
    assert "#" not in fu.verify_cmd


def test_verify_cmd_handles_no_annotation():
    assert mkfu(verify="`git status`").verify_cmd == "git status"


def test_verify_none_is_not_a_command():
    assert mkfu(verify="NONE - not yet articulated").verify_cmd is None
    assert mkfu(verify="NONE - not yet articulated").verify_is_none


# --------------------------------------------------------------------------
# Three-state exit contract. Collapsing UNKNOWN into RED is the "a gate that
# skips reports as a gate that passes" failure, inverted.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("cmd,expected", [
    ("exit 0", "GREEN"),
    ("exit 1", "RED"),
    ("exit 7", "UNKNOWN"),
])
def test_exit_code_contract(cmd, expected):
    assert fu_verify.run_probe(cmd, timeout=10)["verdict"] == expected


def test_timeout_is_unknown_not_red():
    slow = ('powershell -NoProfile -Command "Start-Sleep -Seconds 5"'
            if os.name == "nt" else "sleep 5")
    assert fu_verify.run_probe(slow, timeout=1)["verdict"] == "UNKNOWN"


def test_missing_binary_is_unknown_not_red():
    """cmd.exe returns rc=1 for a command it cannot find. A typo'd predicate
    must never be reported as 'the bug is still present'."""
    assert fu_verify.run_probe(
        "definitely_not_a_real_binary_xyz", timeout=10)["verdict"] == "UNKNOWN"


# --------------------------------------------------------------------------
# A verify is a read-only probe.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("cmd", [
    "rm -rf /", "psql -c 'DELETE FROM servers'", "flyctl deploy -a prod",
    "vast create instance", "git push origin main", "sudo reboot",
])
def test_destructive_predicates_are_refused(cmd):
    assert mkfu(verify="`%s`" % cmd).unsafe_reason() is not None


@pytest.mark.parametrize("cmd", [
    "curl -sf http://127.0.0.1:8772/health",
    "test -f /tmp/marker",
    "git rev-parse HEAD",
    "gh api repos/o/r",
])
def test_read_only_predicates_are_allowed(cmd):
    assert mkfu(verify="`%s`" % cmd).unsafe_reason() is None


# --------------------------------------------------------------------------
# Trust gating: a predicate never observed RED must never close anything.
# --------------------------------------------------------------------------
LEDGER = """### FU-901 | untrusted
- date: 2026-07-28 - source: t - status: open - priority: P1
- class: defect
- detail: d
- verify: `exit 0`
- verify_seen_red: NEVER
- log:
- resolution:


### FU-902 | trusted
- date: 2026-07-28 - source: t - status: open - priority: P1
- class: defect
- detail: d
- verify: `exit 0`
- verify_seen_red: 2026-07-01T00:00:00Z
- log:
- resolution:


### FU-903 | regression
- date: 2026-07-28 - source: t - status: resolved - priority: P1
- class: defect
- detail: d
- verify: `exit 1`
- verify_seen_red: 2026-07-01T00:00:00Z
- log:
- resolution: fixed
"""


def test_green_from_never_red_predicate_does_not_close():
    lines = LEDGER.split("\n")
    results, mutations = fu_verify.sweep(lines, {})
    r = [x for x in results if x["fu"] == "FU-901"][0]
    assert r["verdict"] == "GREEN"
    assert r["action"] == "green-but-untrusted"
    assert not any(m[1] == "FU-901" for m in mutations)


def test_trusted_green_closes_only_after_two_sweeps():
    lines = LEDGER.split("\n")
    state = {}
    _, m1 = fu_verify.sweep(lines, state)
    assert not any(k == "close" and f == "FU-902" for k, f, _ in m1)
    _, m2 = fu_verify.sweep(lines, state)
    assert any(k == "close" and f == "FU-902" for k, f, _ in m2)


def test_red_against_closed_entry_reopens_on_first_failure():
    lines = LEDGER.split("\n")
    _, mutations = fu_verify.sweep(lines, {})
    assert any(k == "reopen" and f == "FU-903" for k, f, _ in mutations)


def test_unknown_neither_advances_nor_resets_a_green_streak():
    lines = ("### FU-904 | unevaluable\n"
             "- date: 2026-07-28 - source: t - status: open - priority: P1\n"
             "- class: defect\n- detail: d\n- verify: `exit 7`\n"
             "- verify_seen_red: 2026-07-01T00:00:00Z\n- log:\n- resolution:\n").split("\n")
    state = {"FU-904": {"greens": 1, "history": []}}
    results, mutations = fu_verify.sweep(lines, state)
    assert results[0]["verdict"] == "UNKNOWN"
    assert state["FU-904"]["greens"] == 1
    assert mutations == []


# --------------------------------------------------------------------------
# Parser must survive the real ledger's separator and status variants.
# --------------------------------------------------------------------------
def test_status_normalises_trailing_prose():
    fu = mkfu(status="resolved (code merged #2022; awaiting prod deploy)")
    assert fu.status == "resolved"
    assert not fu.status_is_clean


def test_reopen_demotes_stale_resolution_text():
    lines = LEDGER.split("\n")
    _, mutations = fu_verify.sweep(lines, {})
    fu_verify.apply_mutations(lines, [m for m in mutations if m[1] == "FU-903"])
    fu = {f.id: f for f in fu_ledger.parse(lines)}["FU-903"]
    assert fu.status == "open"
    assert not (fu.vals.get("resolution") or "").strip()
    assert any("SUPERSEDED" in l for l in lines)
