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


# --- Control-(a): degenerate / unrecognized overall_risk -> UNKNOWN, never published verbatim ---
# A present-but-invalid risk label is corrupt/unassessable input (schema drift, partial write,
# a new model emitting an out-of-vocab label). It must NOT be surfaced to the consumer as a
# verdict, and must NOT silently bypass the trust cap. These poles FAIL against the pre-guard
# code (which did `capped = original` and published 'BANANA'/'  ' verbatim) and pass after.

def test_unrecognized_label_normalized_to_unknown():
    # trusted publisher + garbage label: identity is still trusted, but the risk is UNKNOWN,
    # not the raw 'BANANA' (pre-fix published_overall_risk == 'BANANA').
    r = g("https://github.com/stripe/agent-toolkit", "Stripe", "BANANA")
    assert r["published_overall_risk"] == "UNKNOWN"
    assert r["capped"] is False            # cannot "cap" a non-tier
    assert r["trusted"] is True            # publisher identity is unaffected by the bad label

def test_whitespace_label_not_published_verbatim():
    r = g("https://github.com/stripe/agent-toolkit", "Stripe", "   ")
    assert r["published_overall_risk"] == "UNKNOWN"   # pre-fix leaked '   '

def test_unrecognized_label_untrusted_publisher_is_unknown_not_garbage():
    r = g("https://rando.example.com/mcp", "rando", "SEVERE")
    assert r["published_overall_risk"] == "UNKNOWN"   # pre-fix leaked 'SEVERE'
    assert r["trusted"] is False

def test_recognized_tiers_and_empty_are_untouched():
    # zero-ripple guarantee: every recognized tier + the empty/missing case behaves exactly
    # as before the guard (empty -> '' passthrough, consumer still falls back to labels[...]).
    for tier in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        assert g("https://github.com/nobody/x", "x", tier)["published_overall_risk"] == tier
    assert g("https://github.com/nobody/x", "x", None)["published_overall_risk"] == ""
    assert g("https://github.com/nobody/x", "x", "")["published_overall_risk"] == ""

def test_extended_vocab_tiers_pass_through_not_normalized():
    # INFO/UNKNOWN are recognized (defensive superset) -> a benign extended tier is NEVER
    # mistaken for degenerate input and swept to UNKNOWN. Guards against over-firing.
    assert g("https://github.com/nobody/x", "x", "INFO")["published_overall_risk"] == "INFO"
    assert g("https://github.com/nobody/x", "x", "UNKNOWN")["published_overall_risk"] == "UNKNOWN"
