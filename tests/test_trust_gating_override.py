"""Unit tests for trust_gating_override -- the false-positive / anti-defamation cap.
Pure (no DB). Validated against the live 65,532-row score set on 2026-06-25:
455 official/established servers de-flagged HIGH/CRITICAL -> MEDIUM, 0 false masquerade flags."""
from trust_gating_override import trust_gate


def g(url, name, overall, maint=None):
    return trust_gate(url, name, {"overall_risk": overall, "maintainer_trust": maint})


def test_official_github_org_is_capped():
    r = g("https://github.com/microsoft/mcp", "Microsoft MCP", "CRITICAL")
    assert r["trusted"] and r["capped"] and r["published_overall_risk"] == "MEDIUM"


def test_official_host_is_capped():
    r = g("https://api.stripe.com/v1/mcp", "Stripe API", "HIGH")
    assert r["trusted"] and r["published_overall_risk"] == "MEDIUM"
    assert r["trust_basis"] == "verified_publisher:host"


def test_model_established_maintainer_is_capped():
    r = g("https://github.com/a-rando/tool", "tool", "HIGH", "ESTABLISHED")
    assert r["trusted"] and r["published_overall_risk"] == "MEDIUM"


def test_homoglyph_squat_is_not_trusted_and_flagged():
    r = g("https://github.com/g00gle/maps-mcp", "g00gle maps", "LOW")
    assert not r["trusted"] and not r["capped"]
    assert r["masquerade_flag"] is True


def test_shared_tenant_host_is_not_trusted():
    # anyone can deploy to *.workers.dev -> the suffix proves nothing
    r = g("https://rando.workers.dev/mcp", "cf worker", "HIGH")
    assert not r["trusted"] and r["published_overall_risk"] == "HIGH"


def test_third_party_brand_name_not_flagged_not_trusted():
    # a legit community tool FOR a brand is neither impersonation nor trusted
    r = g("https://github.com/some-rando/google-maps-helper", "google maps helper", "MEDIUM")
    assert not r["trusted"] and not r["masquerade_flag"]
    assert r["published_overall_risk"] == "MEDIUM"


def test_cap_never_raises_risk():
    r = g("https://github.com/microsoft/mcp", "MS", "LOW")
    assert r["published_overall_risk"] == "LOW"


def test_unknown_publisher_passthrough():
    r = g("https://github.com/nobody/whatever", "whatever", "HIGH")
    assert not r["trusted"] and r["published_overall_risk"] == "HIGH"
