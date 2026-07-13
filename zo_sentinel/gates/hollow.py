"""Anti-hollow-scaffold rule -- the ONE definition, imported by every consumer.

A hollow scaffold is a root-level Python module that LOOKS like a feature and
passes syntax/compile checks but is not wired to the real system: a standalone
FastAPI app/router with no app.db/app.models data layer, or one that ships a
mock/placeholder DB. It is the ladder's recurring "passes CI but is fake" output.

This rule is enforced at three seams, in the order the cost is incurred:

  1. goose_runner  (_no_hollow_gate)  -- BEFORE the build is marked .done.
     Cheapest: no artifact, no branch, no PR, no CI, and -- because the
     directive never completes -- no stale .done sentinel to swallow a same-name
     reseed. Records a `no_hollow` lesson so the SAME directive retries in-loop
     with the rejection in context (closed loop), instead of silently vanishing.
  2. publisher     (hollow_scaffold_scan, #1450) -- BEFORE the PR is opened.
     Backstop for artifacts built before this gate existed, or by any other
     producer. Converts a doomed PR into a mesh-visible "hollow_blocked" result.
  3. tests/ci/no_hollow_scaffold.py -- BEFORE the merge. Final backstop; the
     only one that sees the actual PR diff, and the only one an operator cannot
     accidentally switch off.

Keeping the patterns in one module is the point: three copies of a regex are
three chances for the gates to disagree, and a gate the builder can satisfy but
CI cannot is worse than no gate at all. Do not inline these patterns anywhere.
"""
from __future__ import annotations

import re
from typing import Optional

# Mock/placeholder data layer: the module fakes its data instead of reading it.
MOCK = re.compile(r"class\s+Mock|MockDB|mock database|mock data|placeholder|dummy data|"
                  r"simulate fetching|in-memory (db|database)|# *Mock", re.I)
# The module presents an HTTP API surface...
BUILDS_API = re.compile(r"FastAPI\(|APIRouter\(|@app\.(get|post)|@router\.(get|post)")
# ...but a REAL one is bound to the app data layer (mirror verdict_breakdown_api.py).
REAL = re.compile(r"from app\.db|from app\.models|import app\.db|app\.models import|"
                  r"get_session|from app import|import verdict_breakdown_api")


def hollow_scaffold_scan(file_path: str, source: str) -> Optional[str]:
    """Return a human-readable block reason if `source` is a hollow scaffold, else None.

    Scope is deliberately narrow and identical at every seam: only ROOT-LEVEL
    `.py` modules are inspected. Package code (app/, zo_sentinel/, tests/, ...)
    legitimately defines routers and fixtures, and the CI gate only ever sees
    ADDED root-level modules -- so this scan must stay exactly that permissive
    or the builder would be blocked on work CI would happily merge.
    """
    fp = str(file_path or "")
    if "/" in fp or "\\" in fp or not fp.endswith(".py"):
        return None
    if MOCK.search(source):
        return "hollow scaffold: mock/placeholder DB (no-hollow CI would reject)"
    if BUILDS_API.search(source) and not REAL.search(source):
        return ("hollow scaffold: standalone API with no real data layer "
                "(app.db/app.models) (no-hollow CI would reject)")
    return None