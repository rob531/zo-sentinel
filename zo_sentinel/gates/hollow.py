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

import ast
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

# ---------------------------------------------------------------------------
# FU-236: the service-era half of this rule.
#
# The scan below this line was written for the FILE-UNIT era, when every
# candidate artifact was a root-level .py. Its scope guard (`"/" in fp ->
# return None`) therefore returns None for EVERY member of a service unit,
# which since the service era is where the loop's actual output lives. That is
# not a rule that mis-measured; it is a rule structurally incapable of
# observing the population it is now asked to police -- the same shape as
# FU-233's `release_stale_missing`, one layer over.
#
# Measured on origin/main f0146fd2 across ALL 330 service members: exactly 3
# have zero top-level statements, and all three are a single comment naming
# either the exemplar or their own path -- the generating model's deliberation
# written to disk as the deliverable. `python -m <pkg>.contract` exits 0 on
# each, so `promote_staged_to_active.py` records contract_ok=True and admits
# them. A check that CANNOT go red carries no information when it is green.
#
# Deliberately NOT a new required CI check (chairman ruling 2026-07-28:
# stacking a gate beside a gate that does not work is what produced the
# losses). This extends the ONE rule, so all three seams inherit it at once.
# ---------------------------------------------------------------------------

# Members of a service unit that must actually do something. `__init__.py` is
# excluded on purpose: an empty package marker is correct, not hollow.
SERVICE_MEMBER = re.compile(
    r"(?:^|/)services/(?:staged|active)/[^/]+/(contract|logic|router)\.py$")


def _substantive_body(source: str):
    """Top-level statements, minus a module docstring and bare `pass`.

    Returns None if the source does not parse -- a SyntaxError is a different
    gate's finding, and reporting it here would attribute it to the wrong rule.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    body = list(tree.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(getattr(body[0], "value", None), ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]                       # module docstring
    return [n for n in body if not isinstance(n, ast.Pass)]


def _has_assert(tree: ast.AST) -> bool:
    return any(isinstance(n, ast.Assert) for n in ast.walk(tree))


def _has_live_main(tree: ast.AST) -> bool:
    """A `if __name__ == "__main__":` block that actually contains statements."""
    for n in getattr(tree, "body", []):
        if isinstance(n, ast.If) and "__name__" in ast.dump(n.test):
            if [x for x in n.body if not isinstance(x, ast.Pass)]:
                return True
    return False


def hollow_service_member_scan(file_path: str, source: str) -> Optional[str]:
    """Block reason if `source` is a hollow member of a service unit, else None.

    Two limbs, both observed separating the real population from the hollow one
    before either was enforced (see the module header for the measurement):

      1. ANY member with zero top-level statements. Inert by construction --
         it cannot fail, so its exit-0 proves the interpreter runs, not that
         the service exists. 3 of 330 members on main.
      2. A `contract.py` that asserts nothing anywhere AND has no live
         `__main__`. A contract that cannot fail is not a contract.
         3 of 113 contracts on main.
    """
    fp = str(file_path or "").replace("\\", "/")
    m = SERVICE_MEMBER.search(fp)
    if not m:
        return None
    body = _substantive_body(source)
    if body is None:
        return None                            # unparseable: not this rule's call
    if not body:
        return ("hollow service member: zero top-level statements -- the file is "
                "comment/docstring only, so importing it can never fail and its "
                "exit-0 is a liveness proof of the interpreter, not of the service")
    if m.group(1) == "contract":
        tree = ast.parse(source)
        if not (_has_assert(tree) or _has_live_main(tree)):
            return ("hollow contract: no `assert` anywhere and no live "
                    "`__main__` block -- a contract that cannot fail is not a "
                    "contract, and promote_staged_to_active reads its exit-0 "
                    "as contract_ok=True")
    return None


def hollow_scaffold_scan(file_path: str, source: str) -> Optional[str]:
    """Return a human-readable block reason if `source` is a hollow scaffold, else None.

    Scope is deliberately narrow and identical at every seam: only ROOT-LEVEL
    `.py` modules are inspected. Package code (app/, zo_sentinel/, tests/, ...)
    legitimately defines routers and fixtures, and the CI gate only ever sees
    ADDED root-level modules -- so this scan must stay exactly that permissive
    or the builder would be blocked on work CI would happily merge.
    """
    fp = str(file_path or "")
    if not fp.endswith(".py"):
        return None
    # FU-236: service-unit members are nested, so they fall through the
    # root-level guard below. Dispatch them to the rule that CAN see them
    # before that guard discards them -- same entry point, so all three seams
    # inherit this without a fourth gate being added anywhere.
    nested = hollow_service_member_scan(fp, source)
    if nested:
        return nested
    if "/" in fp or "\\" in fp:
        return None
    if MOCK.search(source):
        return "hollow scaffold: mock/placeholder DB (no-hollow CI would reject)"
    if BUILDS_API.search(source) and not REAL.search(source):
        return ("hollow scaffold: standalone API with no real data layer "
                "(app.db/app.models) (no-hollow CI would reject)")
    return None