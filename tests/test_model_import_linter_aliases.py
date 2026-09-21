"""FU-159: the linter's repair map was 3 entries wide and blind to snake_case.

Blocking real defects (FU-031 three-state verdict) is only safe if the MECHANICAL
families self-heal first -- otherwise the factory blocks on drift it could have
fixed. Measured post-#2177 (n=89): of 39 Tier-0 degradations, 9 were mechanically
repairable (Orgs x6, mcp_server_registry x1, mcp_llm_axis_scores x2) and the other
30 were genuine inventions that SHOULD block.
"""

import pytest

from tools import model_import_linter as mil


def test_norm_strips_underscores_so_snake_case_can_match_pascal():
    """The bug: `_norm` lowercased and stripped a plural 's' but kept underscores,
    so a snake_case table name could never normalise onto its model class."""
    assert mil._norm("mcp_server_registry") == mil._norm("McpServerRegistry")
    assert mil._norm("mcp_llm_axis_scores") == mil._norm("McpLlmAxisScore")
    assert mil._norm("Orgs") == mil._norm("Org")


def test_full_model_set_is_wider_than_the_distinctive_set():
    """The distinctive set requires an `Mcp` prefix and >=8 chars, which excluded
    Org, User, ApiKey and every non-Mcp model from ever being repaired."""
    distinctive, full = mil.canonical_models(), mil.all_models()
    assert distinctive <= full
    assert len(full) > len(distinctive)
    assert "Org" in full and "Org" not in distinctive


@pytest.mark.parametrize("wrong,right", [
    ("Orgs", "Org"),
    ("mcp_server_registry", "McpServerRegistry"),
    ("mcp_llm_axis_scores", "McpLlmAxisScore"),
])
def test_repairable_families_are_repaired_when_scoped_to_an_import(wrong, right):
    src = f"from app.models import {wrong}\n\nx = {wrong}\n"
    drift = mil.scan_imports(src, mil.build_map(mil.all_models()))
    assert drift.get(wrong) == right


# --------------------------------------------------------------------------
# NEGATIVE CONTROLS -- the repair must NOT invent a fix for a genuine invention,
# or a broken module would be silently "healed" into a different broken module.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("invented", [
    "mesh_events",            # a table with no model class at all
    "MeshMemory",             # no such model
    "ServiceHealth",          # no such model
    "VulnerabilityLink",      # invented long-form of VulnLink; must NOT map
    "PerspectiveMembership",  # no such model
])
def test_genuine_inventions_are_NOT_repaired_and_stay_RED(invented):
    src = f"from app.models import {invented}\n"
    drift = mil.scan_imports(src, mil.build_map(mil.all_models()))
    assert invented not in drift, (
        f"{invented!r} has no canonical counterpart; 'repairing' it would swap one "
        f"broken import for another and hide a real defect")


def test_widened_pass_is_scoped_to_app_models_imports_only():
    """`Org` is short and common. It is only safe to rewrite `Orgs` because the
    token sits inside a `from app.models import ...` statement."""
    src = "Orgs = 5\nprint(Orgs)\n"          # no app.models import anywhere
    assert mil.scan_imports(src, mil.build_map(mil.all_models())) == {}


def test_distinctive_whole_file_pass_is_unchanged():
    """Original guarantee preserved: whole-file scanning still uses the narrow set."""
    src = "x = MCPServerRegistry\n"
    assert mil.scan_text(src, mil.build_map(mil.canonical_models())) == {
        "MCPServerRegistry": "McpServerRegistry"}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
