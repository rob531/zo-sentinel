"""The regression that let 8 official big-tech servers publish as CRITICAL
(2026-07-04): the tier backfill must ALWAYS route HIGH/CRITICAL through
trust_gating_override. Pure unit tests on the cap helper -- no DB."""
import importlib.util
import os

_p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "tools", "apply_risk_tier_backfill.py")
_spec = importlib.util.spec_from_file_location("tier_backfill", _p)
tier_backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tier_backfill)
cap_for = tier_backfill.cap_for


def test_official_org_critical_is_capped_to_medium():
    got = cap_for("https://github.com/azure/azure-mcp", "Azure MCP", "CRITICAL")
    assert got is not None
    pub, basis = got
    assert pub == "MEDIUM"
    assert basis.startswith("verified_publisher")


def test_official_org_high_is_capped():
    got = cap_for("https://github.com/googleapis/gcloud-mcp", "gcloud", "HIGH")
    assert got == ("MEDIUM", got[1])


def test_random_repo_is_not_capped():
    assert cap_for("https://github.com/some-rando/tool", "tool", "CRITICAL") is None


def test_low_medium_tiers_never_touched():
    assert cap_for("https://github.com/microsoft/mcp", "ms", "MEDIUM") is None
    assert cap_for("https://github.com/microsoft/mcp", "ms", "LOW") is None


def test_homoglyph_squat_is_not_capped():
    # masquerade must NOT inherit the cap
    assert cap_for("https://github.com/g00gle/maps-mcp", "g00gle maps", "CRITICAL") is None
