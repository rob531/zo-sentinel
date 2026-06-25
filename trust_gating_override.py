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

Pure-stdlib, no app/db deps -> embeddable in app_scoring_consumer / verdict_view_api
and unit-testable in isolation.
"""
from __future__ import annotations
import re
import unicodedata

RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
CAP_TIER = "MEDIUM"   # trusted publishers are never published above this

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
    ".azure.com", ".azure.net", ".windows.net", ".cloudflare.com",
    ".supabase.co", ".supabase.com", ".openai.com", ".anthropic.com",
    ".amazonaws.com", ".atlassian.com", ".atlassian.net", ".databricks.com",
    ".twilio.com", ".sendgrid.com", ".shopify.com", ".paypal.com",
)
# Multi-tenant hosts: a verified-looking suffix here proves NOTHING about the
# publisher (anyone can deploy). Never grant host-trust on these.
SHARED_TENANT_SUFFIXES = (
    ".workers.dev", ".vercel.app", ".herokuapp.com", ".onrender.com",
    ".netlify.app", ".pages.dev", ".web.app", ".firebaseapp.com",
    ".azurewebsites.net", ".github.io", ".glama.ai", ".smithery.ai",
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


def _plain(s: str) -> str:
    """NFKC + lower + strip to a-z0-9, WITHOUT homoglyph folding."""
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", s or "").lower())


def _fold(s: str) -> str:
    """_plain plus homoglyph/confusable folding (g00gle -> google)."""
    return _plain((s or "").translate(_HOMOGLYPH))


def github_org(url: str | None) -> str | None:
    m = re.search(r"github\.com/([^/?#]+)", url or "", re.I)
    return m.group(1).lower() if m else None


def host_of(url: str | None) -> str | None:
    m = re.search(r"https?://([^/?#@]+)", url or "", re.I)
    return m.group(1).lower() if m else None


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


def trust_gate(url: str | None, name: str | None, axis_labels: dict) -> dict:
    """Calibrate the published verdict. axis_labels: {axis_name: LABEL} incl.
    'overall_risk' and (ideally) 'maintainer_trust'. Returns the override record."""
    original = (axis_labels.get("overall_risk") or "").upper()
    maint = (axis_labels.get("maintainer_trust") or "").upper()
    org = github_org(url)
    host = host_of(url)

    masquerade = is_masquerade(url, name)
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
    if trust_basis and original in RISK_ORDER and RISK_ORDER[original] > RISK_ORDER[CAP_TIER]:
        capped = CAP_TIER
        changed = True

    if masquerade:
        display = "Possible impersonation of a well-known brand - automated heuristic; treat with caution"
    elif trust_basis:
        display = "High-capability tool from a verified/established maintainer (automated heuristic assessment)"
    else:
        display = "Automated heuristic assessment"

    return {
        "url": url, "name": name,
        "original_overall_risk": original,
        "published_overall_risk": capped,
        "capped": changed,
        "trusted": bool(trust_basis),
        "trust_basis": trust_basis,
        "masquerade_flag": masquerade,
        "display_label": display,
    }
