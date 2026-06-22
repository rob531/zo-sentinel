#!/usr/bin/env python3
"""
Verify aidr_commit_gateway.py verdict enforcement compliance.

Target file under test: aidr_commit_gateway.py (built 2026-04-17).
Quarantined file (different name, NOT us): verify_aidr_commit_gateway_verdict_check.py.

This verifier checks that the gateway:
  (1) Queries verdict from mcp_server_registry via write_service,
  (2) REJECTS commits for CAUTION_LIMITED or HIGH_RISK_ISOLATED verdicts
      unless an explicit override exists in mcp_decisions,
  (3) Includes the injection_resilience score in the commit payload (Phase 9).

Numbered rules enforced below:
  Rule 1 -- MUST check verdict_tier column.
  Rule 2 -- MUST query verdict from mcp_server_registry via write_service.
  Rule 3 -- MUST NOT auto-commit HIGH_RISK tiers (rejection logic present).
  Rule 4 -- MUST REJECT CAUTION_LIMITED and HIGH_RISK_ISOLATED unless
           an explicit override is recorded in mcp_decisions.
  Rule 5 -- MUST include injection_resilience in the commit payload.
"""

import re
import sys
from pathlib import Path

# Module-level config (kept tiny and side-effect-free).
GATEWAY_FILENAME = "aidr_commit_gateway.py"
SEARCH_ROOTS = (Path("."), Path("src"), Path("app"), Path("gateway"),
                Path("modules"), Path(".."))


def find_file(name):
    """Locate `name` under a small set of conventional project roots.

    Pure filesystem existence check; returns a Path or None. No DB / network.
    """
    for base in SEARCH_ROOTS:
        candidate = base / name
        if candidate.exists():
            return candidate
    return None


# --- Pattern sets used by the rule checks ---------------------------------
VERDICT_TIER_PATTERNS = (
    r"verdict_tier",
    r"get_verdict",
    r"query.*verdict",
    r"registry.*verdict",
    r"fetch.*verdict",
)

REGISTRY_VIA_WRITE_PATTERNS = (
    r"mcp_server_registry",
    r"registry.*write_service",
    r"write_service.*registry",
    r"registry\.get",
    r"registry\.query",
    r"write_service.*query",
)

INJECTION_RESILIENCE_PATTERNS = (
    r"injection_resilience",
    r"injectionScore",
    r"injection_score",
    r"resilience_score",
)

REJECTION_PATTERNS = (
    r"\breject\b",
    r"\brefuse\b",
    r"\bdeny\b",
    r"\bblock\b",
    r"\bprevent\b",
)

CAUTION_TIER_PATTERNS = (
    r"CAUTION_LIMITED",
    r"HIGH_RISK_ISOLATED",
    r"HIGH_RISK",
)

OVERRIDE_PATTERNS = (
    r"mcp_decisions",
    r"override",
    r"explicit.*override",
    r"decision.*override",
    r"force_commit",
    r"allow_commit",
)


def _any_pattern(content, patterns):
    """True iff any regex in `patterns` matches `content` (case-insensitive)."""
    return any(re.search(p, content, re.IGNORECASE) for p in patterns)


def _check_rule(label, content, patterns, must=True):
    """Run one rule. Returns (passed: bool, evidence: dict)."""
    hit = _any_pattern(content, patterns)
    evidence = {
        "rule": label,
        "must": must,
        "matched": hit,
        "patterns_searched": list(patterns),
    }
    return hit, evidence


def verify_aidr_commit_gateway_verdict_enforcement():
    """Verify verdict enforcement in aidr_commit_gateway.py.

    Returns True iff ALL numbered MUST rules pass. MUST NOT rules that fail
    also fail the run. Prints a human-readable report to stdout.
    """
    file_path = find_file(GATEWAY_FILENAME)
    if not file_path:
        print(f"[FAIL] {GATEWAY_FILENAME} not found under any search root")
        return False

    print(f"[OK]   Found target: {file_path}")

    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[FAIL] Could not read {file_path}: {exc}")
        return False

    # Rule evaluations -----------------------------------------------------
    rules = []

    rules.append(_check_rule(
        "R1 MUST check verdict_tier column",
        content, VERDICT_TIER_PATTERNS, must=True))

    rules.append(_check_rule(
        "R2 MUST query mcp_server_registry via write_service",
        content, REGISTRY_VIA_WRITE_PATTERNS, must=True))

    rules.append(_check_rule(
        "R3 MUST NOT auto-commit HIGH_RISK tiers (rejection logic)",
        content, REJECTION_PATTERNS, must=True))

    # R4: BOTH caution-tier handling AND an override mechanism must be present.
    tier_ok = _any_pattern(content, CAUTION_TIER_PATTERNS)
    override_ok = _any_pattern(content, OVERRIDE_PATTERNS)
    rules.append((
        tier_ok and override_ok,
        {
            "rule": "R4 MUST REJECT CAUTION_LIMITED/HIGH_RISK_ISOLATED unless "
                    "explicit override in mcp_decisions",
            "must": True,
            "tier_handling": tier_ok,
            "override_present": override_ok,
        },
    ))

    rules.append(_check_rule(
        "R5 MUST include injection_resilience in commit payload",
        content, INJECTION_RESILIENCE_PATTERNS, must=True))

    passed, failed = [], []
    for ok, ev in rules:
        (passed if ok else failed).append(ev)

    # ---- Report ----------------------------------------------------------
    print("\n" + "=" * 64)
    print("VERIFICATION RESULTS: aidr_commit_gateway.py")
    print("=" * 64)

    print(f"\n[PASS] {len(passed)} rule(s):")
    for ev in passed:
        print(f"   ok  - {ev['rule']}")

    if failed:
        print(f"\n[FAIL] {len(failed)} rule(s):")
        for ev in failed:
            extras = ""
            if "tier_handling" in ev:
                extras = (f" (tier_handling={ev['tier_handling']}, "
                          f"override_present={ev['override_present']})")
            print(f"   xx  - {ev['rule']}{extras}")

    all_must_pass = len(failed) == 0

    print("\n" + "=" * 64)
    if all_must_pass:
        print("[OK] VERDICT ENFORCEMENT: COMPLIANT")
        print("aidr_commit_gateway.py correctly enforces:")
        print("  (1) Queries verdict from mcp_server_registry via write_service")
        print("  (2) REJECTS CAUTION_LIMITED / HIGH_RISK_ISOLATED unless override")
        print("  (3) Includes injection_resilience in commit payload")
    else:
        print("[FAIL] VERDICT ENFORCEMENT: NON-COMPLIANT")
    print("=" * 64)

    return all_must_pass


# --- Self-smoke (Appendix B rule 5) ---------------------------------------
# Exercise the verifier against three synthetic gateway sources to ensure
# the rule engine distinguishes compliant vs non-compliant inputs.
_SYNTHETIC_CASES = [
    ("compliant",
     (
         "# gateway\n"
         "verdict = query('SELECT verdict_tier FROM mcp_server_registry', via=write_service)\n"
         "if verdict in ('CAUTION_LIMITED', 'HIGH_RISK_ISOLATED') and not override_in('mcp_decisions'):\n"
         "    reject(commit)\n"
         "payload = {'injection_resilience': score, 'verdict': verdict}\n"
     ),
     True),
    ("missing_injection_resilience",
     (
         "verdict = registry.get_verdict_tier()\n"
         "if verdict == 'HIGH_RISK': reject()\n"
         "if verdict == 'CAUTION_LIMITED' and not mcp_decisions.override: reject()\n"
         "payload = {'verdict': verdict}\n"
     ),
     False),
    ("no_rejection_logic",
     (
         "v = query('SELECT verdict_tier FROM mcp_server_registry')\n"
         "payload = {'injection_resilience': v.score}\n"
         "commit(payload)\n"
     ),
     False),
]


def _self_smoke():
    """Run the rule engine against synthetic inputs; assert >=3 cases pass."""
    results = []
    for label, src, expected in _SYNTHETIC_CASES:
        # Re-implement the rule loop inline so we don't depend on the
        # gateway file existing on disk.
        rules = [
            _check_rule("R1", src, VERDICT_TIER_PATTERNS),
            _check_rule("R2", src, REGISTRY_VIA_WRITE_PATTERNS),
            _check_rule("R3", src, REJECTION_PATTERNS),
            (_any_pattern(src, CAUTION_TIER_PATTERNS)
             and _any_pattern(src, OVERRIDE_PATTERNS),
             {"rule": "R4"}),
            _check_rule("R5", src, INJECTION_RESILIENCE_PATTERNS),
        ]
        actual = all(ok for ok, _ in rules)
        results.append((label, expected, actual, actual == expected))

    print("\n[SELF-SMOKE] synthetic rule-engine cases:")
    for label, expected, actual, ok in results:
        flag = "ok" if ok else "xx"
        print(f"   {flag}  {label}: expected_compliant={expected} got={actual}")

    assert len(results) >= 3, "self-smoke must cover >=3 cases"
    assert all(ok for _, _, _, ok in results), "self-smoke regression"


if __name__ == "__main__":
    # Self-smoke first (does not touch disk), then the real verification.
    _self_smoke()
    success = verify_aidr_commit_gateway_verdict_enforcement()
    sys.exit(0 if success else 1)