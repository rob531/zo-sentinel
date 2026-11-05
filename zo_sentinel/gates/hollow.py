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


def _binds_name(tree: ast.AST, name: str) -> bool:
    """True iff `name` is a MODULE-LEVEL bound symbol -- an assignment target
    (`name = ...`, `name: T = ...`), an import alias, or a def/class named
    `name`. Walks only the top-level body: a router bound inside a function is
    not the module-level attribute the spine mounts.
    """
    for n in getattr(tree, "body", []):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return True
        elif isinstance(n, ast.AnnAssign):
            if isinstance(n.target, ast.Name) and n.target.id == name:
                return True
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if n.name == name:
                return True
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                if (a.asname or a.name.split(".")[0]) == name:
                    return True
    return False


def hollow_service_member_scan(file_path: str, source: str) -> Optional[str]:
    """Block reason if `source` is a hollow member of a service unit, else None.

    Three limbs, each observed separating the real population from the hollow
    one before it was enforced (see the module header for the measurement):

      1. ANY member with zero top-level statements. Inert by construction --
         it cannot fail, so its exit-0 proves the interpreter runs, not that
         the service exists. 3 of 330 members on main.
      2. A `contract.py` that asserts nothing anywhere AND has no live
         `__main__`. A contract that cannot fail is not a contract.
         3 of 113 contracts on main.
      3. A `router.py` that parses with >=1 top-level statement but binds no
         module-level `router` symbol. This is the dominant permanent leak:
         service_dir_from_exemplar handed the generating model the directive
         as a DESCRIPTION, and on an empty/refused/mis-shaped generation the
         loop wrote the model's non-code output -- a bare path string
         (`services/staged/<n>/router.py`), a JSON `{"path": ...}` blob, the
         deliberation prose, or logic.py content mislanded in router.py -- to
         disk. Each PARSES (so the Tier-0 syntax gate passes it) and has a
         non-empty body (so limb 1 misses it), but exposes no `router =
         APIRouter(...)`, so generate_spine/include_spine cannot mount it and
         the service is permanently un-promotable. Measured 2026-11-05 across
         all staged router.py: 63 of 2590 bind no `router` (10 more are
         zero-body/limb-1, 10 more SyntaxError/syntax-gate). The exemplar it
         mirrors (services/_exemplar/router.py) binds `router = APIRouter(...)`
         at module level, so this is the exact shape the directive asked for.
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
    if m.group(1) == "router":
        tree = ast.parse(source)
        if not _binds_name(tree, "router"):
            return ("hollow router: parses but binds no module-level `router` "
                    "symbol -- a router.py MUST expose `router = APIRouter(...)` "
                    "(the attribute generate_spine/include_spine mounts), mirror "
                    "services/_exemplar/router.py. The written content is not a "
                    "router module (a path string, a JSON blob, the generating "
                    "model's prose, or logic.py content mislanded here), so the "
                    "service can never be mounted or promoted.")
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


# ---------------------------------------------------------------------------
# Two-pole merge gate (mirrors tools/spine_mount_router.py --self-test).
#
# Pole B: non-code garbage mislanded in a service member -- the bare path
#         string, JSON blob, prose, control-token sludge and logic-in-router
#         blobs observed in prod (2026-11-05) -- is REJECTED (scan returns a
#         reason, so the writeback refuses and the directive re-queues).
# Pole A: a real exemplar-mirrored router.py and logic.py PASS (scan returns
#         None) and are allowed to persist.
# Deterministic, no network, no DB: the fixtures are fixed strings.
# ---------------------------------------------------------------------------

# Byte-exact prod examples (services/staged/<n>/router.py on the running box,
# 2026-11-05). Each PARSES as python and the Tier-0 syntax gate passed it; the
# router limb is what separates them from a real module.
_POLE_B_ROUTER = {
    "bare path string": "services/staged/app_scoring_consumer_base/router.py",
    "json path blob": '{"path": "services/staged/cve_risk_tier_cross_api/router.py"}',
    "exemplar-ref comment": "# services/_exemplar/router.py\n",
    "logic content mislanded in router.py": (
        "# services/staged/axis_score_drift/logic.py\n"
        "from datetime import datetime, timedelta\n"
        "from typing import List, Optional\n"
        "def compute_drift(a, b):\n"
        "    return a - b\n"
    ),
}

# A real router.py that mirrors services/_exemplar/router.py -- binds a
# module-level `router = APIRouter(...)` the spine can mount.
_POLE_A_ROUTER = (
    '"""router.py -- the service HTTP surface."""\n'
    "from __future__ import annotations\n"
    "from fastapi import APIRouter, Depends\n"
    "from sqlalchemy.orm import Session\n"
    "from app.db import get_session\n"
    "from .logic import risk_tier_histogram\n"
    'router = APIRouter(prefix="/api/example", tags=["example"])\n'
    '@router.get("/histogram")\n'
    "def get_histogram(db: Session = Depends(get_session)):\n"
    "    return risk_tier_histogram(db)\n"
)

# A real, non-trivial logic.py.
_POLE_A_LOGIC = (
    '"""logic.py -- the data layer."""\n'
    "from __future__ import annotations\n"
    "from sqlalchemy import func, select\n"
    "from sqlalchemy.orm import Session\n"
    "from app.models import McpServerRegistry\n"
    "def risk_tier_histogram(db: Session) -> dict:\n"
    "    return {}\n"
)


def self_test() -> int:
    """Two-pole merge gate for the generate_file / service_dir_from_exemplar
    shape-lint. Returns 0 on PASS, 1 on FAIL."""
    ok = True

    def ck(label: str, cond: bool) -> None:
        nonlocal ok
        print(("  [GREEN] " if cond else "  [RED]   ") + label)
        ok = ok and cond

    rp = "services/staged/widget/router.py"
    lp = "services/staged/widget/logic.py"

    print("=== POLE B (garbage / path-string / empty -> expect REJECT) ===")
    for label, src in _POLE_B_ROUTER.items():
        why = hollow_service_member_scan(rp, src)
        ck(f"Pole B router: {label!r} -> REJECTED", why is not None and "hollow" in why)
        # And the SAME garbage must be rejected via the shared entry point all
        # three seams call (R2: the fix must arm at the seam, not just the lib).
        ck(f"Pole B router (via hollow_scaffold_scan): {label!r} -> REJECTED",
           hollow_scaffold_scan(rp, src) is not None)

    # zero-statement router (comment-only / bare path with no newline parses to
    # a single Expr, but a pure comment is zero-body -- limb 1 catches it too).
    ck("Pole B router: comment-only (zero body) -> REJECTED",
       hollow_service_member_scan(rp, "# just a note about the exemplar\n") is not None)

    print("\n=== POLE A (real exemplar-mirrored code -> expect PASS) ===")
    ck("Pole A router: binds `router = APIRouter(...)` -> PASS",
       hollow_service_member_scan(rp, _POLE_A_ROUTER) is None)
    ck("Pole A router (via hollow_scaffold_scan) -> PASS",
       hollow_scaffold_scan(rp, _POLE_A_ROUTER) is None)
    ck("Pole A logic: non-trivial data layer -> PASS",
       hollow_service_member_scan(lp, _POLE_A_LOGIC) is None)
    # A router importing `router` (relative/alias) rather than assigning it is
    # still a real binding -- must not false-fail.
    ck("Pole A router: `from .x import router` binding -> PASS",
       hollow_service_member_scan(rp, "from .app import router\n") is None)

    print("\n=== NO FALSE-FAIL (behaviour the fix must not change) ===")
    # Unparseable content is a DIFFERENT gate's finding (the syntax gate), not
    # this rule's -- it must stay None here so the attribution is correct.
    ck("unparseable prose -> None (syntax gate's finding, not ours)",
       hollow_service_member_scan(rp, "Let me first check the exemplar router\n") is None)
    # logic.py carries no router obligation.
    ck("logic.py with no `router` -> PASS (router limb is router-only)",
       hollow_service_member_scan(lp, "X = 1\n") is None)
    # non-service paths are untouched.
    ck("app/x.py (non-service) -> None",
       hollow_service_member_scan("app/router_helpers.py", "X = 1\n") is None)

    print("\n=== SUMMARY ===")
    if ok:
        print(f"SELF-TEST PASS: Pole B {len(_POLE_B_ROUTER)+1}/{len(_POLE_B_ROUTER)+1} REJECT | "
              f"Pole A 4/4 PASS | no false-fail 3/3")
        return 0
    print("SELF-TEST FAIL")
    return 1


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="anti-hollow-scaffold rule (the ONE definition)")
    ap.add_argument("--self-test", action="store_true",
                    help="two-pole merge gate for the service-member shape-lint")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    ap.print_help()
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main())