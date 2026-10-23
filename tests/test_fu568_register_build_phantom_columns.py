"""FU-568: register_build must refuse a .py that names a phantom COLUMN.

Background, measured 2026-09-29 against origin/main a7bc32da6 -- the basis, not
a headline:

    referent_verify   routes PASS (ARMED) | tables PASS (ARMED) | columns FAIL 113
    builder .py registrations in trailing 45d ......... 1022
    ... that name a column existing on no plane ......   10  (1.0%)
    referents so named ...............................   13
    ... also flagged by referent_verify (the judge) ...   13  (0 false positives)
    most recent .....................................  2026-09-18, three files

The tables half of #4080 went 82 -> 0 because #4068 blocked the shape AT
EMISSION. The columns half has no emission block, so 16 new phantom column
referents were added in September alone while cycles hand-cured two. A backlog
refilled by the path that produced it cannot be emptied by curing the backlog.

This suite execs the SHIPPED helpers out of builder_mcp.py in a stdlib-only
namespace and drives them with the REAL resolver out of tools/referent_verify.py,
so the assertions run the real bytes on both sides rather than pattern-matching
a paragraph. builder_mcp.py itself cannot be imported in CI (httpx / mcp are not
installed), which is why the helpers were written as pure functions.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER_MCP = REPO_ROOT / "mcp_servers" / "builder_mcp.py"
REFERENT_VERIFY = REPO_ROOT / "tools" / "referent_verify.py"

HELPERS = ("_phantom_column_refs", "_reject_phantom_columns")

# Verbatim from services/staged/scoring_backlog_health_api/router.py, the
# builder artifact registered on 2026-09-18 (#5234). Three phantoms in one
# statement: mcp_server_registry has no `server_name`, mcp_llm_axis_scores has
# no `created_at`, cadence_job_runs has no `server_id`.
PHANTOM_ROUTER_PY = '''
from sqlalchemy import text

query = text("""
    WITH server_scores AS (
        SELECT
            r.server_id,
            r.server_name,
            MAX(s.created_at) AS latest_score_at
        FROM mcp_server_registry r
        LEFT JOIN mcp_llm_axis_scores s ON r.server_id = s.server_id
        LEFT JOIN cadence_job_runs c ON r.server_id = c.server_id
        GROUP BY r.server_id, r.server_name
    )
    SELECT * FROM server_scores
""")
'''

# Same shape, real columns only.
CLEAN_ROUTER_PY = '''
from sqlalchemy import text

query = text("""
    SELECT r.name, r.last_scanned
    FROM mcp_server_registry r
    WHERE r.name IS NOT NULL
""")
'''

# A table on NO plane. The TABLES check owns this one and is already armed;
# refusing it here too would double-judge one defect.
UNKNOWN_TABLE_PY = '''
from sqlalchemy import text

query = text("SELECT z.whatever FROM totally_imaginary_table z")
'''


def _fn_source(src: str, name: str) -> str:
    tree = ast.parse(src)
    lines = src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return "\n".join(lines[node.lineno - 1: node.end_lineno])
    return ""


@pytest.fixture(scope="module")
def shipped_src() -> str:
    assert BUILDER_MCP.is_file(), f"builder_mcp.py not found at {BUILDER_MCP}"
    return BUILDER_MCP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def guard(shipped_src):
    """The shipped helpers, exec'd with the stdlib alone."""
    ns: dict = {"ast": ast}
    for name in HELPERS:
        fn_src = _fn_source(shipped_src, name)
        assert fn_src, (
            f"{name} not found in builder_mcp.py -- register_build has no column "
            "referent check, so a query naming a column that exists on no plane "
            "registers cleanly and publishes (FU-568)."
        )
        exec(compile(fn_src, f"<fu568-{name}>", "exec"), ns)  # noqa: S102
    return ns


@pytest.fixture(scope="module")
def resolver():
    """The REAL catalog + extractors out of the single judge."""
    assert REFERENT_VERIFY.is_file(), f"missing {REFERENT_VERIFY}"
    spec = importlib.util.spec_from_file_location("_rv_fu568", REFERENT_VERIFY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_rv_fu568"] = mod
    spec.loader.exec_module(mod)
    catalog, _meta, unknown = mod.load_catalog()
    if unknown or not catalog:
        pytest.skip(f"catalog unresolvable in this environment: {unknown!r}")
    return catalog, mod._iter_sql_strings, mod.extract_refs


# --------------------------------------------------------------------------
# 1. The defect, reproduced against the shipped code and the real catalog
# --------------------------------------------------------------------------

def test_refuses_the_real_20260918_payload(guard, resolver):
    catalog, it, ex = resolver
    out = guard["_reject_phantom_columns"](
        "services/staged/scoring_backlog_health_api/router.py",
        PHANTOM_ROUTER_PY, catalog, it, ex)
    assert out, ("the verbatim 2026-09-18 builder artifact was NOT refused -- "
                 "the emission path is still free to refill the #4080 columns "
                 "backlog (FU-568)")
    assert out.startswith("REGISTER_ERROR:"), out
    for ref in ("mcp_server_registry.server_name",
                "mcp_llm_axis_scores.created_at",
                "cadence_job_runs.server_id"):
        assert ref in out, f"{ref} missing from the refusal: {out}"


def test_refusal_names_the_real_columns(guard, resolver):
    """R7: a refusal that does not carry the correction is a wall to route around."""
    catalog, it, ex = resolver
    out = guard["_reject_phantom_columns"](
        "services/staged/x/router.py", PHANTOM_ROUTER_PY, catalog, it, ex)
    assert "Real columns:" in out, out
    assert "last_scanned" in out, out          # a genuine mcp_server_registry column
    assert "FU-568" in out, out


# --------------------------------------------------------------------------
# 2. The guard must not over-reach -- 1.0% is the measured budget
# --------------------------------------------------------------------------

def test_allows_real_columns(guard, resolver):
    catalog, it, ex = resolver
    assert guard["_reject_phantom_columns"](
        "services/staged/x/router.py", CLEAN_ROUTER_PY, catalog, it, ex) == ""


def test_does_not_double_judge_an_unknown_table(guard, resolver):
    """referent-verify's TABLES check is armed and owns this case."""
    catalog, it, ex = resolver
    assert guard["_reject_phantom_columns"](
        "services/staged/x/router.py", UNKNOWN_TABLE_PY, catalog, it, ex) == ""


def test_ignores_non_py_targets(guard, resolver):
    catalog, it, ex = resolver
    assert guard["_reject_phantom_columns"](
        "services/staged/x/query.sql", PHANTOM_ROUTER_PY, catalog, it, ex) == ""


def test_handles_windows_separators(guard, resolver):
    catalog, it, ex = resolver
    out = guard["_reject_phantom_columns"](
        "services\\staged\\x\\router.py", PHANTOM_ROUTER_PY, catalog, it, ex)
    assert out.startswith("REGISTER_ERROR:"), out


# --------------------------------------------------------------------------
# 3. UNKNOWN NEVER REFUSES -- the branch that keeps a stale snapshot from
#    stopping every build on the host, asserted rather than assumed.
# --------------------------------------------------------------------------

def test_empty_catalog_allows_and_never_refuses(guard, resolver):
    _catalog, it, ex = resolver
    assert guard["_reject_phantom_columns"](
        "services/staged/x/router.py", PHANTOM_ROUTER_PY, {}, it, ex) == ""
    assert guard["_reject_phantom_columns"](
        "services/staged/x/router.py", PHANTOM_ROUTER_PY, None, None, None) == ""


def test_unparseable_py_is_not_this_guards_verdict(guard, resolver):
    """_reject_unparseable_py (FU-565) owns syntax; this must stay silent."""
    catalog, it, ex = resolver
    assert guard["_reject_phantom_columns"](
        "services/staged/x/router.py", "def broken(:\n    pass\n",
        catalog, it, ex) == ""


# --------------------------------------------------------------------------
# 4. The wiring: the pure function must actually be CALLED by register_build.
#    FU-565's own lesson -- a check present and never consulted is not a check.
# --------------------------------------------------------------------------

def test_register_build_calls_the_guard(shipped_src):
    body = shipped_src.split("async def register_build", 1)[1]
    assert "_reject_phantom_columns(" in body, (
        "_reject_phantom_columns exists but register_build never calls it")
    assert "_load_referent_resolver(" in body
    assert "referents UNCHECKED" in body, (
        "an unresolvable catalog must be ANNOUNCED on the REGISTERED line, "
        "never swallowed -- unknown is not zero (HARNESS_DOCTRINE R6)")


def test_guard_runs_after_the_compile_check(shipped_src):
    """Order matters: ast.parse on unparseable content would be wasted work."""
    body = shipped_src.split("async def register_build", 1)[1]
    assert body.index("_reject_unparseable_py(") < body.index("_reject_phantom_columns(")
