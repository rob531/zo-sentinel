"""Unit tests for trust_gating_override -- the false-positive / anti-defamation cap.
Pure (no DB). Validated against the live 65,532-row score set on 2026-06-25:
455 official/established servers de-flagged HIGH/CRITICAL -> MEDIUM, 0 false masquerade flags."""
from trust_gating_override import github_org, host_of, trust_gate


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


# --- Control-(b): URL authority & GitHub org spoofing resistance (ARGON-D1-71) ---
# Pre-fix regexes (`re.search(r"github\.com/...")` and `re.search(r"https?://([^/?#@]+)")`)
# allowed any attacker-controlled domain to claim verified_publisher trust and downgrade
# CRITICAL -> MEDIUM via path/query/fragment injection, prefix-domain spoofing, or `@` userinfo.

def test_github_path_query_fragment_injection_is_not_trusted():
    for spoof in (
        "https://evil.com/github.com/stripe/backdoor",
        "https://evil.com/?q=github.com/microsoft/backdoor",
        "https://evil.com/#github.com/openai/backdoor",
    ):
        r = g(spoof, "evil-mcp", "CRITICAL")
        assert not r["trusted"] and not r["capped"]
        assert r["published_overall_risk"] == "CRITICAL"
        assert github_org(spoof) is None


def test_github_prefix_domain_and_noncanonical_subdomain_not_trusted():
    for spoof in (
        "https://notgithub.com/openai/backdoor",
        "https://evil.github.com/stripe/backdoor",
        "https://api.github.com/stripe/backdoor",
    ):
        r = g(spoof, "evil-mcp", "CRITICAL")
        assert not r["trusted"] and not r["capped"]
        assert r["published_overall_risk"] == "CRITICAL"
        assert github_org(spoof) is None


def test_userinfo_authority_spoof_is_not_trusted():
    for spoof in (
        "https://api.stripe.com@evil.com/mcp",
        "https://github.com@evil.com/stripe/mcp",
        "https://user:pass@api.stripe.com/mcp",
    ):
        r = g(spoof, "evil-mcp", "CRITICAL")
        assert not r["trusted"] and not r["capped"]
        assert r["published_overall_risk"] == "CRITICAL"
        assert host_of(spoof) is None


def test_backslash_authority_confusion_is_not_trusted():
    for spoof in (
        "https://evil.com\\@api.stripe.com/mcp",
        "https://api.stripe.com\\@evil.com/mcp",
    ):
        r = g(spoof, "evil-mcp", "CRITICAL")
        assert not r["trusted"] and not r["capped"]
        assert r["published_overall_risk"] == "CRITICAL"
        assert host_of(spoof) is None


def test_schemeless_embedded_url_in_path_is_not_trusted():
    spoof = "evil.com/https://api.stripe.com/mcp"
    r = g(spoof, "evil-mcp", "CRITICAL")
    assert not r["trusted"] and not r["capped"]
    assert r["published_overall_risk"] == "CRITICAL"
    assert host_of(spoof) is None


def test_official_host_with_explicit_valid_port_is_capped():
    r = g("https://api.stripe.com:443/v1/mcp", "Stripe API", "CRITICAL")
    assert r["trusted"] and r["capped"]
    assert r["trust_basis"] == "verified_publisher:host"
    assert r["published_overall_risk"] == "MEDIUM"
    assert host_of("https://api.stripe.com:443/v1/mcp") == "api.stripe.com"


def test_official_host_with_invalid_port_fails_closed():
    for bad in ("https://api.stripe.com:99999/mcp", "https://api.stripe.com:abc/mcp"):
        r = g(bad, "Stripe API", "CRITICAL")
        assert not r["trusted"] and not r["capped"]
        assert r["published_overall_risk"] == "CRITICAL"
        assert host_of(bad) is None


def test_git_plus_https_and_www_github_remain_trusted():
    for legit in (
        "git+https://github.com/stripe/agent-toolkit",
        "https://www.github.com/microsoft/mcp",
        "github.com/openai/codex-mcp",
    ):
        r = g(legit, "Official MCP", "CRITICAL")
        assert r["trusted"] and r["capped"]
        assert r["trust_basis"] == "verified_publisher:github_org"
        assert r["published_overall_risk"] == "MEDIUM"


# --- Control-(a): non-mapping axis_labels and non-string fields fail closed without crashing ---

def test_non_mapping_axis_labels_fails_closed_to_unknown():
    for bad in (None, ["CRITICAL"], "CRITICAL", 42):
        r = trust_gate("https://github.com/stripe/agent-toolkit", "Stripe", bad)
        assert r["published_overall_risk"] == "UNKNOWN"
        assert r["capped"] is False


def test_non_string_overall_risk_fails_closed_to_unknown():
    for bad in (123, float("nan"), True, ["HIGH"], {"label": "HIGH"}):
        r = trust_gate("https://github.com/stripe/agent-toolkit", "Stripe", {"overall_risk": bad})
        assert r["published_overall_risk"] == "UNKNOWN"
        assert r["capped"] is False


def test_non_string_maintainer_trust_and_url_never_crash():
    r1 = trust_gate("https://example.com/mcp", "tool", {"overall_risk": "HIGH", "maintainer_trust": 999})
    assert not r1["trusted"] and r1["published_overall_risk"] == "HIGH"
    r2 = trust_gate(12345, 67890, {"overall_risk": "HIGH"})  # type: ignore[arg-type]
    assert not r2["trusted"] and r2["published_overall_risk"] == "HIGH"
    assert r2["url"] is None and r2["name"] is None


def test_api_github_urls_are_not_extracted_as_github_orgs():
    assert github_org("https://api.github.com/graphql") is None
    assert github_org("https://api.github.com/repos/stripe/agent-toolkit") is None
