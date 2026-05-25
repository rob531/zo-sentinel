#!/usr/bin/env python3
"""
fix_external_api_pydantic_constraints.py

Three surgical edits to sentinel_external_api.py. The API starts cleanly
and /v1/health returns 200, but /v1/search returns 500 because MiniMax
wrote the Pydantic model with the wrong numeric range for trust_score.

Root cause: trust_score is stored in DuckDB as a 0-100 composite, not a
0-1 probability. Pydantic validator rejects every real row.

Patches:

  A. MCPAssessment.trust_score:  ge=0.0, le=1.0  ->  ge=0.0, le=100.0
  B. SearchResult.trust_score:   ge=0.0, le=1.0  ->  ge=0.0, le=100.0
  C. MCPAssessment.verdict description: the outdated 4-tier list is
     replaced with the real 6-tier taxonomy so your colleague reading
     the API docs gets truth not fiction.

NOT touched:
  - MCPAssessment.confidence (ge=0.0, le=1.0) -- verified 0-1 in DB.
  - risk_tier field (Optional[str]) -- no range issue.

Idempotent. AST-validated. Backup on write.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_sentinel/sentinel_external_api.py")

# ---- Patch A: MCPAssessment.trust_score ------------------------------------
A_OLD = "    trust_score: Optional[float] = Field(None, ge=0.0, le=1.0)\n    verdict_reasoning: Optional[str] = Field(None, description='Explanation of verdict')"
A_NEW = "    trust_score: Optional[float] = Field(None, ge=0.0, le=100.0, description='Composite trust score 0-100')\n    verdict_reasoning: Optional[str] = Field(None, description='Explanation of verdict')"

# ---- Patch B: SearchResult.trust_score -------------------------------------
B_OLD = "    server_id: str\n    name: Optional[str] = None\n    verdict: Optional[str] = None\n    trust_score: Optional[float] = Field(None, ge=0.0, le=1.0)"
B_NEW = "    server_id: str\n    name: Optional[str] = None\n    verdict: Optional[str] = None\n    trust_score: Optional[float] = Field(None, ge=0.0, le=100.0, description='Composite trust score 0-100')"

# ---- Patch C: verdict description string -----------------------------------
C_OLD = "    verdict: Optional[str] = Field(None, description='TRUSTED/CONDITIONAL/UNTRUSTED/UNKNOWN')"
C_NEW = "    verdict: Optional[str] = Field(None, description='One of: TRUSTED_GENERAL, TRUSTED_RESEARCH, ENTERPRISE_CONTROLLED, CAUTION_LIMITED, HIGH_RISK_ISOLATED, KNOWN_THREAT, INSUFFICIENT')"


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("sentinel_external_api: fix trust_score Pydantic range (0-1 -> 0-100)")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2
    src = TARGET.read_text()
    changed = False

    # Patch A+B (idempotency check first)
    if "ge=0.0, le=100.0, description='Composite trust score 0-100'" in src and src.count("ge=0.0, le=100.0") >= 2:
        print("  [skip A,B] trust_score constraints already widened to 0-100")
    else:
        if A_OLD in src:
            src = src.replace(A_OLD, A_NEW, 1)
            print("  [patch A] MCPAssessment.trust_score: le=1.0 -> le=100.0")
            changed = True
        else:
            print("  [FAIL A] MCPAssessment.trust_score anchor not found verbatim")
            return 2

        if B_OLD in src:
            src = src.replace(B_OLD, B_NEW, 1)
            print("  [patch B] SearchResult.trust_score: le=1.0 -> le=100.0")
            changed = True
        else:
            print("  [FAIL B] SearchResult.trust_score anchor not found verbatim")
            return 2

    # Patch C
    if "TRUSTED_GENERAL, TRUSTED_RESEARCH" in src:
        print("  [skip C] verdict description already on 6-tier taxonomy")
    elif C_OLD in src:
        src = src.replace(C_OLD, C_NEW, 1)
        print("  [patch C] verdict description updated to real 6-tier taxonomy")
        changed = True
    else:
        print("  [WARN C] verdict description anchor not found (non-fatal)")

    if not changed:
        print("\n  [noop] all patches already applied")
        return 0

    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"  [FAIL] AST invalid after patch: {e}")
        return 2

    _backup(TARGET)
    TARGET.write_text(src)
    print(f"\n  [done] {TARGET.name} patched")
    print("\nRestart:")
    print("  pkill -9 -f 'sentinel_external_api.py'")
    print("  sleep 2")
    print("  nohup python3 /home/workspace/zo_sentinel/sentinel_external_api.py \\")
    print("    >> /home/workspace/logs/sentinel_external_api.log 2>&1 &")
    print("  sleep 3")
    print("  curl -s -H 'X-API-Key: test-key-robin-apr18' \\")
    print("    'http://127.0.0.1:8791/v1/search?q=github&limit=3'")
    return 0


if __name__ == "__main__":
    sys.exit(main())