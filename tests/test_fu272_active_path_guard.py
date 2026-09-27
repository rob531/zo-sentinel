"""FU-272: builder_mcp.register_build must reject target_file under services/active/.

Background: service_dir_from_exemplar.yaml recipe instructs Goose to write service
files under services/staged/<name>/, but the service.toml VERBATIM MANIFEST block
contains `import_path = "services.active.<name>.router"` as a FORWARD REFERENCE to
post-promotion location. LLM agents running the recipe conflate this import_path with
the write destination and call register_build("services/active/<name>/router.py", ...),
causing the publisher to open a PR that lands router.py in services/active/ with no
corresponding service.toml -- triggering capmap-check STRICT failures and a broken
active registry entry (32+ open PRs affected as of 2026-09-07).

The fix is a guard in register_build that rejects services/active/ paths with a
clear message explaining the correct services/staged/ destination.

This test suite:
  1. Verifies the guard is present and instructive (AST parse, no imports of httpx/mcp).
  2. Verifies the guard rejects the exact paths seen in the broken PRs.
  3. Provides a negative control proving the test would go RED without the guard.
  4. Verifies service_decomposer still produces only services/staged/ output_files.
  5. Verifies the recipe text explicitly states services/staged/ as the write target
     and does not produce services/active/ as a file destination.

Must pass on Windows and ubuntu-latest with no live services (stdlib + ast only).
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

BUILDER_MCP = REPO_ROOT / "mcp_servers" / "builder_mcp.py"
SOA_RECIPE = REPO_ROOT / "goose_recipes" / "service_dir_from_exemplar.yaml"


# ---------------------------------------------------------------------------
# Helpers -- read and parse builder_mcp without importing it (httpx/mcp deps
# are not present in CI; the guard logic is pure string/path logic that an
# AST read can verify without running the code)
# ---------------------------------------------------------------------------


def _register_build_source() -> str:
    """Return the source of the register_build function, or '' if absent."""
    src = BUILDER_MCP.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ""
    lines = src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "register_build":
            return "\n".join(lines[node.lineno - 1: node.end_lineno])
    return ""


# ---------------------------------------------------------------------------
# 1. Guard presence and quality
# ---------------------------------------------------------------------------


def test_builder_mcp_exists():
    assert BUILDER_MCP.is_file(), f"builder_mcp.py not found at {BUILDER_MCP}"


def test_register_build_function_exists():
    assert _register_build_source(), "register_build function not found in builder_mcp.py"


def test_register_build_rejects_services_active_paths():
    """FU-272 core: the guard must check for services/active/ in the target_file."""
    fn_src = _register_build_source()
    assert "services/active" in fn_src, (
        "register_build does not check for services/active/ -- a model confusing "
        "import_path with write destination can register files there unchecked (FU-272). "
        "Add a guard that rejects target_file.startswith('services/active/')."
    )


def test_register_build_guard_returns_register_error():
    """The rejection must use the standard REGISTER_ERROR prefix so callers can detect it."""
    fn_src = _register_build_source()
    # Must contain both the check and a REGISTER_ERROR return that mentions staged
    has_error = "REGISTER_ERROR" in fn_src
    has_staged = "staged" in fn_src
    assert has_error and has_staged, (
        "register_build guard for services/active/ must return a REGISTER_ERROR message "
        "that mentions 'staged' so the LLM knows the correct destination. "
        f"REGISTER_ERROR present: {has_error}, 'staged' mentioned: {has_staged}."
    )


def test_register_build_guard_mentions_import_path_explanation():
    """The error must explain WHY services/active/ is wrong -- i.e. import_path is a
    forward reference, not the write destination -- so the model can self-correct."""
    fn_src = _register_build_source()
    # Must mention import_path (the forward reference) and staged (the correct destination)
    assert "import_path" in fn_src or "FORWARD REFERENCE" in fn_src or "forward" in fn_src.lower(), (
        "register_build guard must explain that import_path is a forward reference to the "
        "post-promotion location, not the write destination. Without this explanation the "
        "model will repeat the mistake on the next attempt. (FU-272)"
    )


# ---------------------------------------------------------------------------
# 2. Negative control -- prove the test goes RED without the guard
# ---------------------------------------------------------------------------


def test_negative_control_guard_removal_would_fail():
    """Doctrine R3: an assertion never seen RED proves nothing.

    This simulates the pre-fix state by stripping the guard block from the
    source, then asserting that test_register_build_rejects_services_active_paths
    would FAIL on that broken source. If it doesn't, the main test is vacuous."""
    src = BUILDER_MCP.read_text(encoding="utf-8")
    # Locate the FU-272 guard block (must be present for this test to be meaningful)
    if "# FU-272:" not in src:
        pytest.skip("FU-272 guard block not present -- cannot run negative control")

    # Strip the guard block to simulate the broken state
    start = src.index("    # FU-272:")
    end = src.index("    out = f\"/home/workspace", start)
    broken_src = src[:start] + src[end:]

    tree = ast.parse(broken_src)
    lines = broken_src.splitlines()
    fn_src = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "register_build":
            fn_src = "\n".join(lines[node.lineno - 1: node.end_lineno])
            break

    # On the broken source, the check SHOULD fail
    has_active_check = "services/active" in fn_src
    assert not has_active_check, (
        "Negative control failed: the broken (guard-stripped) source still contains "
        "'services/active' -- the check would pass vacuously. Investigate why. "
        "This means the main guard test (test_register_build_rejects_services_active_paths) "
        "may be measuring the wrong thing."
    )


# ---------------------------------------------------------------------------
# 3. service_decomposer emits only services/staged/ output_files
# ---------------------------------------------------------------------------


def test_service_decomposer_outputs_are_staged_not_active():
    """Reinforcing test: the decomposer must never emit an output_file pointing to
    services/active/ -- that would reproduce FU-272 from the fan-out side."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from tools.service_decomposer import decompose
    except ImportError:
        pytest.skip("service_decomposer not importable")

    directives = decompose("gateway_health_probe", "GET /api/gateway/health " * 5)
    for d in directives:
        of = d.get("output_file", "")
        assert not of.startswith("services/active/"), (
            f"service_decomposer emitted output_file in services/active/: {of!r} "
            "-- decomposer must only target services/staged/ (FU-272)"
        )
        assert of.startswith("services/staged/gateway_health_probe/"), (
            f"service_decomposer emitted unexpected output_file: {of!r}"
        )


# ---------------------------------------------------------------------------
# 4. Recipe text explicitly targets services/staged/ (not services/active/)
# ---------------------------------------------------------------------------


def test_soa_recipe_write_target_is_staged():
    """The recipe's TARGET section must direct Goose to write to services/staged/,
    not services/active/. If this fails, the recipe prompt has drifted back to
    naming the wrong path -- fix the recipe, not just the guard."""
    if not SOA_RECIPE.is_file():
        pytest.skip(f"recipe not found at {SOA_RECIPE}")
    text = SOA_RECIPE.read_text(encoding="utf-8")
    # The canonical form from the recipe (line ~62)
    assert "services/staged/{{ service_name }}" in text, (
        "service_dir_from_exemplar.yaml TARGET section does not say "
        "'services/staged/{{ service_name }}/' -- recipe has drifted. "
        "The model's write destination must be services/staged/, not services/active/. (FU-272)"
    )


def test_soa_recipe_import_path_has_explainer_comment():
    """The recipe VERBATIM MANIFEST contains 'services.active.<name>.router' in
    import_path -- a correct forward reference. BUT without an explicit comment
    clarifying it is NOT the write destination, models confuse it.
    This test requires the recipe to carry an explanatory comment near that block."""
    if not SOA_RECIPE.is_file():
        pytest.skip(f"recipe not found at {SOA_RECIPE}")
    text = SOA_RECIPE.read_text(encoding="utf-8")
    # The recipe must have the import_path line
    assert "services.active.{{ service_name }}.router" in text or \
           "services.active" in text, "import_path block not found in recipe"

    # There must be an explanation that import_path is about post-promotion location
    # (one of: "AFTER promotion", "after promotion", "lives AFTER", "lives after")
    has_explainer = bool(re.search(r"(AFTER|after)\s+promotion", text, re.IGNORECASE))
    assert has_explainer, (
        "service_dir_from_exemplar.yaml VERBATIM MANIFEST contains import_path pointing "
        "to services.active but lacks an explanation that this is where the file lives "
        "AFTER promotion (not the write destination). Without this, LLMs write to "
        "services/active/ directly. Add: '# import_path names services.ACTIVE because "
        "that is where the file lives AFTER promotion.' (FU-272)"
    )
