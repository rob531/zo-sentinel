"""trust_gating_override.py -- deterministic post-process that stops the
overall_risk head from defaming well-known / official MCP publishers.

WHY: the SFT student's overall_risk head conflates inherent SURFACE (broad
capability x sensitive data x external egress) with THREAT, so it labels the
*official* Stripe / Microsoft / Azure / Google-Cloud / Cloudflare / Supabase
MCP servers HIGH or CRITICAL. That is inaccurate and is trade-libel exposure
(see false_positive_audit.md). This layer CAPS the published verdict for
verified or established publishers and reframes it as "high-capability, trusted"
-- WITHOUT a model retrain.

MASQUERADE-SAFE: trust is granted ONLY on an EXACT match against a curated
allow-list of verified publishers (GitHub org or official host), or on the
model's own maintainer_trust in {ESTABLISHED, VERIFIED}. A homoglyph / typosquat
of a well-known brand (g00gleMCP, micr0soft, stripe-payouts.tk) therefore gets
NO trust pass -- it never matches the exact allow-list -- and is additionally
FLAGGED as a possible impersonation so it can be surfaced, not hidden.

URL-AUTHORITY HARDENING (Control-b): github_org() and host_of() parse URLs with
RFC 3986 urllib.parse.urlsplit anchored strictly to the URL scheme + authority
(rejecting userinfo `@`, backslash `\\`, control/whitespace chars, invalid ports,
prefix-domain spoofs like `notgithub.com`, non-canonical subdomains like
`evil.github.com` / `api.github.com`, and path/query/fragment injections like
`https://evil.com/github.com/stripe/pwn`).

DEGENERATE-INPUT GUARD (Control-a): non-mapping axis_labels and non-string or
out-of-vocabulary overall_risk values never crash or pass verbatim -- they fail
closed to `UNKNOWN` with `capped=False`.

Pure-stdlib, no app/db deps -> embeddable in app_scoring_consumer / verdict_view_api
and unit-testable in isolation.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
import sys
import unicodedata
from urllib.parse import urlsplit

RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
CAP_TIER = "MEDIUM"   # trusted publishers are never published above this
# Recognized overall_risk vocabulary for the Control-(a) degenerate-input guard. The model's
# overall_risk head emits exactly RISK_ORDER's 4 tiers (every live row is LOW/MEDIUM/HIGH/
# CRITICAL); INFO/UNKNOWN are included as a defensive superset of every risk-tier token seen
# across the consumers so a benign extended tier can NEVER be misread as degenerate. A label
# outside this set is corrupt/unassessable -> UNKNOWN (see trust_gate).
RECOGNIZED_RISK = frozenset(RISK_ORDER) | {"INFO", "UNKNOWN"}

# --- verified official GitHub orgs (EXACT, case-insensitive; never substring) ---
VERIFIED_GITHUB_ORGS = frozenset({
    "microsoft", "azure", "azure-samples", "mssql", "dotnet",
    "google", "googleapis", "googlecloudplatform", "google-gemini", "google-ai-edge",
    "stripe", "cloudflare", "supabase", "vercel", "netlify",
    "aws", "awslabs", "amazon-web-services", "aws-samples",
    "anthropics", "openai", "modelcontextprotocol", "huggingface",
    "slackapi", "atlassian", "notionhq", "linear", "asana",
    "elastic", "grafana", "hashicorp", "redis", "mongodb", "mongodb-developer",
    "docker", "getsentry", "datadog", "twilio", "sendgrid",
    "github", "gitlab", "paypal", "shopify", "heroku", "digitalocean",
    "oracle", "ibm", "salesforce", "confluentinc", "snowflakedb", "databricks",
    "jetbrains", "postmanlabs", "neo4j", "pinecone-io", "qdrant", "weaviate-io",
})

# --- verified official hosts (EXACT host or dotted-suffix match) ---
VERIFIED_HOST_SUFFIXES = (
    ".stripe.com", ".googleapis.com", ".cloud.google.com", ".microsoft.com",
    ".azure.com", ".azure.net", ".cloudflare.com",
    ".supabase.com", ".openai.com", ".anthropic.com",
    ".atlassian.com", ".atlassian.net", ".databricks.com",
    ".twilio.com", ".sendgrid.com", ".shopify.com", ".paypal.com",
)
# Multi-tenant hosts: a verified-looking suffix here proves NOTHING about the
# publisher (anyone can deploy). Never grant host-trust on these.
SHARED_TENANT_SUFFIXES = (
    ".workers.dev", ".vercel.app", ".herokuapp.com", ".onrender.com",
    ".netlify.app", ".pages.dev", ".web.app", ".firebaseapp.com",
    ".azurewebsites.net", ".github.io", ".glama.ai", ".smithery.ai",
    # Provider namespaces where ANY member of the public can provision a host
    # (S3 buckets, Azure Storage accounts, Supabase projects) -- the suffix
    # proves the HOSTER, never the publisher identity. The vendors' own
    # identity is verified via VERIFIED_GITHUB_ORGS (aws/awslabs, supabase)
    # or their single-tenant corporate suffix (.supabase.com), not these.
    ".amazonaws.com", ".windows.net", ".supabase.co",
)

# brand tokens we defend against impersonation (homoglyph / typosquat)
WELL_KNOWN_BRANDS = frozenset({
    "google", "microsoft", "stripe", "cloudflare", "supabase", "paypal",
    "github", "gitlab", "amazon", "azure", "openai", "anthropic", "notion",
    "slack", "atlassian", "oracle", "salesforce", "shopify", "heroku",
    "mongodb", "redis", "docker", "vercel", "databricks", "snowflake",
})

_HOMOGLYPH = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s",
                            "7": "t", "8": "b", "$": "s", "@": "a", "|": "l"})

_CANONICAL_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})


def _plain(s: str | None) -> str:
    """NFKC + lower + strip to a-z0-9, WITHOUT homoglyph folding."""
    if not isinstance(s, str):
        return ""
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", s).lower())


def _fold(s: str | None) -> str:
    """_plain plus homoglyph/confusable folding (g00gle -> google)."""
    if not isinstance(s, str):
        return ""
    return _plain(s.translate(_HOMOGLYPH))


def _parse_http_url(url: str | None, *, allow_schemeless_github: bool = False) -> tuple[str, str] | None:
    """Strict RFC 3986 parser anchored to scheme + authority.

    Rejects userinfo (`@`), backslashes (`\\`), whitespace/control characters,
    malformed/out-of-range ports, and non-HTTP(S) schemes so an untrusted path,
    query, fragment, or authority-confusion payload can never spoof a verified
    publisher identity.
    """
    if not isinstance(url, str):
        return None
    s = url.strip()
    if not s:
        return None
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in s):
        return None

    low = s.lower()
    if low.startswith(("git+https://", "git+http://")):
        s = s[4:]
    elif allow_schemeless_github and "://" not in s and low.startswith(("github.com/", "www.github.com/")):
        s = "https://" + s

    try:
        parsed = urlsplit(s)
    except ValueError:
        return None

    if parsed.scheme.lower() not in ("http", "https"):
        return None

    netloc = parsed.netloc
    if not netloc or "@" in netloc or "\\" in netloc:
        return None

    try:
        # Validate port strictly (rejects non-numeric and >65535 ports).
        _ = parsed.port
        host = parsed.hostname
    except ValueError:
        return None

    if not host:
        return None

    host_norm = host.lower().rstrip(".")
    if not host_norm or ".." in host_norm or host_norm.startswith("."):
        return None

    return host_norm, parsed.path


def github_org(url: str | None) -> str | None:
    """Return the GitHub org/owner ONLY when the URL authority is strictly
    github.com or www.github.com (never prefix-domains, subdomains, userinfo,
    or path/query/fragment injections)."""
    parsed = _parse_http_url(url, allow_schemeless_github=True)
    if parsed is None:
        return None
    host, path = parsed
    if host not in _CANONICAL_GITHUB_HOSTS:
        return None
    stripped = path.lstrip("/")
    if not stripped:
        return None
    org = stripped.split("/", 1)[0]
    if not org or org in (".", ".."):
        return None
    return org.lower()


def host_of(url: str | None) -> str | None:
    """Return the canonical lowercase hostname (port stripped) ONLY for a
    well-formed http(s) URL with no userinfo (`@`) or backslash authority."""
    parsed = _parse_http_url(url, allow_schemeless_github=False)
    return parsed[0] if parsed is not None else None


def _host_verified(host: str | None) -> bool:
    if not host:
        return False
    if any(host == s.lstrip(".") or host.endswith(s) for s in SHARED_TENANT_SUFFIXES):
        return False
    return any(host == s.lstrip(".") or host.endswith(s) for s in VERIFIED_HOST_SUFFIXES)


def is_masquerade(url: str | None, name: str | None) -> bool:
    """True only for a genuine HOMOGLYPH / digit-substitution squat: a token that
    FOLDS to a protected brand but is NOT spelled as that brand and is NOT the
    verified publisher (g00gle, micr0soft, paypa1). A token that is simply the
    real brand word (redis, slack, a 3rd-party 'google-maps-helper') is NOT a
    masquerade -- impersonation requires the spelling to differ yet fold to the brand."""
    org = github_org(url)
    if org and org in VERIFIED_GITHUB_ORGS:
        return False
    if _host_verified(host_of(url)):
        return False
    # Only the IDENTITY tokens matter (github org + host labels), not the freeform name.
    cands = []
    if org:
        cands.append(org)
    h = host_of(url)
    if h:
        cands += [lbl for lbl in h.split(".") if lbl not in ("com", "io", "ai", "net", "org", "co", "dev", "app", "www")]
    for c in cands:
        folded, plain = _fold(c), _plain(c)
        # squat = folding turned it INTO a brand, but it isn't spelled as that brand
        if folded in WELL_KNOWN_BRANDS and plain != folded:
            return True
    return False


def trust_gate(url: str | None, name: str | None, axis_labels: Mapping | None) -> dict:
    """Calibrate the published verdict. axis_labels: {axis_name: LABEL} incl.
    'overall_risk' and (ideally) 'maintainer_trust'. Returns the override record."""
    safe_url = url if isinstance(url, str) else None
    safe_name = name if isinstance(name, str) else None

    if not isinstance(axis_labels, Mapping):
        original = ""
        maint = ""
        unrecognized_label = True
    else:
        raw_risk = axis_labels.get("overall_risk")
        raw_maint = axis_labels.get("maintainer_trust")
        maint = raw_maint.upper() if isinstance(raw_maint, str) else ""

        if raw_risk is None or raw_risk == "":
            original = ""
            unrecognized_label = False
        elif isinstance(raw_risk, str):
            original = raw_risk.upper()
            unrecognized_label = bool(original) and original not in RECOGNIZED_RISK
        else:
            # Control-(a): non-string overall_risk (int, float/NaN, bool, list, dict)
            # is degenerate/corrupt input -> fail closed to UNKNOWN, never crash.
            original = str(raw_risk)
            unrecognized_label = True

    org = github_org(safe_url)
    host = host_of(safe_url)

    masquerade = is_masquerade(safe_url, safe_name)
    trust_basis = None
    if not masquerade:
        if org and org in VERIFIED_GITHUB_ORGS:
            trust_basis = "verified_publisher:github_org"
        elif _host_verified(host):
            trust_basis = "verified_publisher:host"
        elif maint in ("ESTABLISHED", "VERIFIED"):
            trust_basis = "model_maintainer_" + maint.lower()

    capped = original
    changed = False
    if (not unrecognized_label) and trust_basis and original in RISK_ORDER and RISK_ORDER[original] > RISK_ORDER[CAP_TIER]:
        capped = CAP_TIER
        changed = True

    # Control-(a) degenerate-input guard: a PRESENT overall_risk label that is not a
    # recognized tier (or a non-mapping axis_labels / non-string overall_risk) is corrupt
    # or unassessable input, not a verdict. Normalize it to the explicit UNKNOWN sentinel
    # (mirroring risk_tier_from_axes' canonical UNKNOWN) instead of publishing the raw
    # garbage verbatim or crashing. A truthy sentinel also short-circuits the consumer's
    # `gate["published_overall_risk"] or labels[...]` fallback, so an invalid label cannot
    # leak downstream either. Scope is safe-by-construction: the empty/missing case
    # (original == "" with Mapping axis_labels) is left as "" (consumer falls back as
    # before), and every recognized tier -- including the defensive INFO/UNKNOWN superset --
    # passes through verbatim, so no real row's verdict can change.
    if unrecognized_label:
        capped = "UNKNOWN"
        changed = False

    if masquerade:
        display = "Possible impersonation of a well-known brand - automated heuristic; treat with caution"
    elif unrecognized_label:
        display = "Unrecognized risk label - unassessable (automated heuristic; treat as UNKNOWN)"
    elif trust_basis:
        display = "High-capability tool from a verified/established maintainer (automated heuristic assessment)"
    else:
        display = "Automated heuristic assessment"

    return {
        "url": safe_url, "name": safe_name,
        "original_overall_risk": original,
        "published_overall_risk": capped,
        "capped": changed,
        "trusted": bool(trust_basis),
        "trust_basis": trust_basis,
        "masquerade_flag": masquerade,
        "display_label": display,
    }


def _self_test() -> int:
    checks: list[tuple[str, bool]] = []

    def check(label: str, cond: bool) -> None:
        checks.append((label, bool(cond)))

    # Legitimate publishers stay capped
    r_gh = trust_gate("https://github.com/stripe/agent-toolkit", "Stripe", {"overall_risk": "CRITICAL"})
    check("legit_github_org_capped", r_gh["trusted"] and r_gh["capped"] and r_gh["published_overall_risk"] == "MEDIUM")

    r_host_port = trust_gate("https://api.stripe.com:443/mcp", "Stripe", {"overall_risk": "CRITICAL"})
    check("legit_host_with_port_capped", r_host_port["trusted"] and r_host_port["trust_basis"] == "verified_publisher:host" and r_host_port["published_overall_risk"] == "MEDIUM")

    r_git_plus = trust_gate("git+https://github.com/microsoft/mcp", "MS", {"overall_risk": "HIGH"})
    check("legit_git_plus_github_capped", r_git_plus["trusted"] and r_git_plus["published_overall_risk"] == "MEDIUM")

    # Control-(b): URL authority & GitHub org spoofing vectors MUST NOT grant trust
    spoofs = [
        ("path_inject_github", "https://evil.com/github.com/stripe/backdoor"),
        ("query_inject_github", "https://evil.com/?q=github.com/microsoft/backdoor"),
        ("fragment_inject_github", "https://evil.com/#github.com/openai/backdoor"),
        ("prefix_domain_github", "https://notgithub.com/openai/backdoor"),
        ("subdomain_github", "https://evil.github.com/stripe/backdoor"),
        ("api_github_not_org", "https://api.github.com/stripe/backdoor"),
        ("userinfo_host_spoof", "https://api.stripe.com@evil.com/mcp"),
        ("backslash_host_spoof", "https://evil.com\\@api.stripe.com/mcp"),
        ("schemeless_path_host", "evil.com/https://api.stripe.com/mcp"),
        ("invalid_port_host", "https://api.stripe.com:99999/mcp"),
    ]
    for sname, surl in spoofs:
        sr = trust_gate(surl, "evil", {"overall_risk": "CRITICAL"})
        check(f"spoof_blocked_{sname}", (not sr["trusted"]) and (not sr["capped"]) and sr["published_overall_risk"] == "CRITICAL")

    # Control-(a): degenerate axis_labels & non-string fields fail closed to UNKNOWN without crashing
    r_none = trust_gate("https://github.com/stripe/mcp", "Stripe", None)
    check("ctrl_a_none_axis_labels", r_none["published_overall_risk"] == "UNKNOWN" and not r_none["capped"])

    r_list = trust_gate("https://github.com/stripe/mcp", "Stripe", ["CRITICAL"])  # type: ignore[arg-type]
    check("ctrl_a_list_axis_labels", r_list["published_overall_risk"] == "UNKNOWN" and not r_list["capped"])

    r_int = trust_gate("https://github.com/stripe/mcp", "Stripe", {"overall_risk": 123})
    check("ctrl_a_int_overall_risk", r_int["published_overall_risk"] == "UNKNOWN" and not r_int["capped"])

    r_nan = trust_gate("https://github.com/stripe/mcp", "Stripe", {"overall_risk": float("nan")})
    check("ctrl_a_nan_overall_risk", r_nan["published_overall_risk"] == "UNKNOWN" and not r_nan["capped"])

    r_bad_maint = trust_gate("https://example.com/mcp", "x", {"overall_risk": "HIGH", "maintainer_trust": 123})
    check("ctrl_a_bad_maint_no_crash", (not r_bad_maint["trusted"]) and r_bad_maint["published_overall_risk"] == "HIGH")

    r_ws = trust_gate("https://github.com/stripe/mcp", "Stripe", {"overall_risk": "   "})
    check("ctrl_a_whitespace_unknown", r_ws["published_overall_risk"] == "UNKNOWN")

    r_empty = trust_gate("https://github.com/nobody/x", "x", {"overall_risk": ""})
    check("zero_ripple_empty_passthrough", r_empty["published_overall_risk"] == "")

    failed = [name for name, ok in checks if not ok]
    print(f"trust_gating_override self_test: {len(checks) - len(failed)}/{len(checks)} PASS (failed={len(failed)})")
    for name in failed:
        print(f"  FAIL: {name}")
    return 0 if not failed else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(_self_test())
    raise SystemExit(0)
