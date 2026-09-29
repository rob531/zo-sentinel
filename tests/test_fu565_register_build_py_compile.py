"""FU-565: register_build must refuse a .py artifact that does not parse.

Background, measured 2026-09-29 against the LIVE build host
(/home/workspace/zo_sentinel), not a repo path:

    services/active : 458 .py files, 20 do not parse, 15 of those are markup
                      (`<!DOCTYPE html>` as the first bytes of router.py)
    services/staged : 6631 .py files, 58 do not parse, 0 markup

The markup files exist ONLY in the promoted tier -- the tier
app/_spine_generated.py is generated from. register_build is the provenance
hook every goose build passes through, and its docstring asked the caller to
run `python -m py_compile` first. FU-272 later taught it to check the
destination PATH, but nothing ever checked the CONTENT, so an HTML document
saved as router.py registered cleanly and published.

This suite execs the SHIPPED helper source in a stdlib-only namespace and
calls it, so the assertions run the real bytes rather than pattern-matching a
paragraph. builder_mcp.py itself cannot be imported in CI (httpx / mcp are not
installed), which is why the helper was written as a pure function.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER_MCP = REPO_ROOT / "mcp_servers" / "builder_mcp.py"

# Verbatim shape of the real defect: the first 12 lines of
# services/active/high_risk_servers_dashboard_view/router.py on the live host.
# SyntaxError is raised at `margin: 20px;` -- "invalid decimal literal".
HTML_AS_ROUTER_PY = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>High Risk Servers Dashboard</title>
    <style>
        /* Inline CSS */
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
        }
    </style>
</head>
</html>
"""

VALID_ROUTER_PY = """from fastapi import APIRouter

router = APIRouter()


@router.get("/api/servers/high-risk")
async def high_risk():
    return {"servers": []}
"""


def _helper_source(src: str) -> str:
    """Return the source of _reject_unparseable_py from the given module text."""
    tree = ast.parse(src)
    lines = src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_reject_unparseable_py":
            return "\n".join(lines[node.lineno - 1: node.end_lineno])
    return ""


def _load(src: str):
    """Exec the helper in an isolated stdlib-only namespace and return it."""
    fn_src = _helper_source(src)
    if not fn_src:
        return None
    ns: dict = {}
    exec(compile(fn_src, "<fu565-helper>", "exec"), ns)  # noqa: S102
    return ns["_reject_unparseable_py"]


@pytest.fixture(scope="module")
def shipped_src() -> str:
    assert BUILDER_MCP.is_file(), f"builder_mcp.py not found at {BUILDER_MCP}"
    return BUILDER_MCP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def guard(shipped_src):
    fn = _load(shipped_src)
    assert fn is not None, (
        "_reject_unparseable_py not found in builder_mcp.py -- register_build has "
        "no content check, so an HTML document can register as a .py build "
        "artifact and publish into services/active (FU-565)."
    )
    return fn


# --------------------------------------------------------------------------
# 1. The defect, reproduced against the shipped code
# --------------------------------------------------------------------------

def test_rejects_html_saved_as_router_py(guard):
    out = guard("services/staged/high_risk_servers_dashboard_view/router.py",
                HTML_AS_ROUTER_PY)
    assert out, "an HTML document registered as router.py was NOT rejected (FU-565)"
    assert out.startswith("REGISTER_ERROR:"), out
    assert "does not parse as Python" in out, out


def test_rejection_names_the_markup_remedy(guard):
    """The message must be actionable or the model repeats it next attempt."""
    out = guard("services/staged/x/router.py", HTML_AS_ROUTER_PY)
    assert "template" in out.lower(), out
    assert "FU-565" in out, out


def test_rejects_ordinary_syntax_error(guard):
    out = guard("services/staged/x/logic.py", "def broken(:\n    pass\n")
    assert out.startswith("REGISTER_ERROR:"), out
    assert "py_compile" in out, out


# --------------------------------------------------------------------------
# 2. The guard must not over-reach
# --------------------------------------------------------------------------

def test_accepts_valid_python(guard):
    assert guard("services/staged/x/router.py", VALID_ROUTER_PY) == ""


def test_ignores_non_py_targets(guard):
    """An .html template is markup on purpose -- the guard is scoped to .py."""
    assert guard("services/staged/x/templates/dash.html", HTML_AS_ROUTER_PY) == ""
    assert guard("services/staged/x/service.toml", "name = 'x'\n") == ""


def test_handles_windows_separators(guard):
    out = guard("services\\staged\\x\\router.py", HTML_AS_ROUTER_PY)
    assert out.startswith("REGISTER_ERROR:"), out


def test_null_bytes_do_not_escape_as_valueerror(guard):
    """compile() raises ValueError, not SyntaxError, on embedded NULs."""
    out = guard("services/staged/x/router.py", "x = 1\x00\n")
    assert out.startswith("REGISTER_ERROR:"), out


# --------------------------------------------------------------------------
# 3. NEGATIVE CONTROL -- doctrine R4
# --------------------------------------------------------------------------

def test_negative_control_without_the_guard_the_html_sails_through(shipped_src):
    """Strip the guard body and prove the suite above would be vacuous without it.

    An assertion never observed RED is an untested branch. Here the pre-fix
    state is reconstructed by neutering the helper to the `return ""` it
    effectively was before FU-565, and the SAME payload that
    test_rejects_html_saved_as_router_py rejects is shown to pass.
    """
    fn_src = _helper_source(shipped_src)
    assert fn_src, "guard absent -- negative control cannot run"

    pre_fix = 'def _reject_unparseable_py(target_file, content):\n    return ""\n'
    ns: dict = {}
    exec(compile(pre_fix, "<fu565-prefix>", "exec"), ns)  # noqa: S102
    unguarded = ns["_reject_unparseable_py"]

    assert unguarded("services/staged/x/router.py", HTML_AS_ROUTER_PY) == "", (
        "negative control is broken: the pre-fix stub must accept the payload"
    )
    guarded = _load(shipped_src)
    assert guarded("services/staged/x/router.py", HTML_AS_ROUTER_PY) != "", (
        "the shipped guard accepts what the pre-fix stub accepts -- it "
        "discriminates nothing (FU-565)"
    )


def test_register_build_actually_calls_the_guard(shipped_src):
    """A helper nothing calls is a dark tool. Prove the call site exists."""
    tree = ast.parse(shipped_src)
    lines = shipped_src.splitlines()
    body = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "register_build":
            body = "\n".join(lines[node.lineno - 1: node.end_lineno])
    assert body, "register_build not found"
    assert "_reject_unparseable_py(" in body, (
        "_reject_unparseable_py is defined but register_build never calls it -- "
        "the check is present and structurally incapable of firing (FU-565)."
    )
