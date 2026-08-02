"""FU-158 — the E9 forbidden-token check must match TOKENS, not substrings.

`unsafe_reason()` tested each forbidden token with a bare `tok in low`. The token
`"dd "` therefore matched inside `git worktree add --detach`, and FU-158's
read-only `pytest` predicate was reported as a disk-destroying command on every
lint run. A checker that cries wolf on a safe predicate gets muted, and a muted
checker protects nothing — so the false positives below are as load-bearing as
the true positives, and both halves are asserted here.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fu_ledger  # noqa: E402


def _unsafe(verify: str) -> bool:
    fu = fu_ledger.FU(num="999", title="t", start=0, end=1)
    fu.vals["verify"] = verify
    return fu.unsafe_reason() is not None


# ---- NEGATIVE CONTROLS: read-only probes that must NOT be refused -----------
SAFE = [
    # the exact FU-158 predicate that was falsely flagged ('add ' contains 'dd ')
    "`git worktree add --detach /tmp/x && cd /tmp/x && python -m pytest tests -q`",
    # 'perform ' / 'platform ' contain 'rm '
    "`python -c \"import platform; print(platform.system())\"`",
    "`echo perform a read-only check`",
    # 'removed' contains 'rm ' only with the space, but 'confirm ' does too
    "`grep -c confirm /var/log/app.log`",
    # a plain SQL read
    "`psql -tAc \"select count(*) from mcp_server_registry\"`",
    # 'commit ' / 'improve ' are not mutations
    "`git log --oneline -1`",
]

# ---- POSITIVE CONTROLS: these must still be refused -------------------------
UNSAFE = [
    "`dd if=/dev/zero of=/dev/sda`",
    "`rm -rf /tmp/x`",
    "`sudo reboot`",
    "`echo x >> /etc/hosts`",
    "`git clean --force`",
    "`psql -c \"delete from mcp_server_registry\"`",
    "`flyctl deploy --app mcplookup`",
    "`kill 1234`",
    "`chmod 777 /etc`",
]


def test_safe_predicates_are_not_refused():
    for cmd in SAFE:
        assert not _unsafe(cmd), "false E9 on a read-only probe: %s" % cmd


def test_mutating_predicates_are_still_refused():
    for cmd in UNSAFE:
        assert _unsafe(cmd), "forbidden command slipped through: %s" % cmd


def test_boundary_rule_does_not_weaken_punctuation_tokens():
    # ">>" has no word boundary to anchor on; it must stay a plain substring
    # match or a redirect glued to a word would escape.
    assert _unsafe("`python x.py>>/etc/passwd`")


if __name__ == "__main__":
    fails = 0
    for cmd in SAFE:
        bad = _unsafe(cmd)
        fails += bad
        print(("FAIL" if bad else "pass"), "safe  ", cmd[:70])
    for cmd in UNSAFE:
        bad = not _unsafe(cmd)
        fails += bad
        print(("FAIL" if bad else "pass"), "unsafe", cmd[:70])
    bad = not _unsafe("`python x.py>>/etc/passwd`")
    fails += bad
    print(("FAIL" if bad else "pass"), "unsafe", "glued redirect")
    print("\nSELF-TEST", "PASS" if not fails else "FAIL (%d)" % fails)
    sys.exit(1 if fails else 0)
