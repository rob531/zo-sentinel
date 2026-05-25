import re
import sys
from typing import Dict

SHADOW_MCP_PATHS = [
    "/mcp",
    "/mcp/",
    "/mcp/sse",
    "/mcp/stream",
    "/api/mcp",
    "/api/mcp/sse",
    "/v1/mcp",
    "/v1/mcp/sse",
    "/proxy/mcp",
    "/proxy/mcp/sse",
]

SHADOW_PATH_PATTERNS = [
    r"^/mcp$",
    r"^/mcp/",
    r"^/api/mcp",
    r"^/v1/mcp",
    r"^/proxy/mcp",
]

KNOWN_MCP_HOSTS = {
    "mcp.stripe.com",
    "mcp.cloudflare.com",
    "mcp.github.com",
    "mcp.openai.com",
    "mcp.anthropic.com",
    "mcp.netscape.com",
    "mcp.browserbase.com",
    "mcp.temporal.io",
    "mcp.notion.so",
    "mcp.slack.com",
    "mcp.linear.app",
    "mcp.vercel.com",
    "mcp.supabase.com",
    "mcp.resend.com",
    "mcp.stripe.com",
    "mcp.sentry.io",
    "mcp.launchdarkly.com",
    "mcp.datadog.com",
    "mcp.pagerduty.com",
    "mcp.contentful.com",
    "mcp.shopify.com",
    "mcp.hubspot.com",
    "mcp.twilio.com",
    "mcp.sendgrid.com",
    "mcp.firebase.com",
    "mcp.mongodb.com",
    "mcp.postgresql.com",
    "mcp.redis.com",
    "mcp.kubernetes.io",
    "mcp.docker.com",
    "mcp.aws.amazon.com",
    "mcp.gcp.googleapis.com",
    "mcp.azure.com",
}

MCP_SUBDOMAIN_PREFIXES = [
    "mcp",
    "mcp-",
    "mcp_",
]

SUSPICIOUS_TLDS = {
    ".tk", ".ml", ".ga", ".cf", ".gq",
    ".xyz", ".top", ".biz", ".info", ".online",
    ".site", ".website", ".space", ".work", ".click",
}

_COMPILED_PATH_PATTERNS = [re.compile(p) for p in SHADOW_PATH_PATTERNS]

_MCP_SUBDOMAIN_RE = re.compile(r"^(mcp[-_]?)")

_SUSPICIOUS_TLD_RE = re.compile(r"(" + "|".join(re.escape(tld) for tld in SUSPICIOUS_TLDS) + r")$", re.IGNORECASE)


def is_shadow_mcp_url(path: str) -> bool:
    if not path:
        return False
    path = path.strip().lower()
    if path in SHADOW_MCP_PATHS:
        return True
    for pattern in _COMPILED_PATH_PATTERNS:
        if pattern.match(path):
            return True
    return False


def is_shadow_mcp_hostname(hostname: str) -> bool:
    if not hostname:
        return False
    hostname = hostname.strip().lower()
    if hostname in KNOWN_MCP_HOSTS:
        return True
    parts = hostname.split(".")
    if len(parts) >= 2:
        subdomain = parts[0]
        if _MCP_SUBDOMAIN_RE.match(subdomain):
            return True
    if _SUSPICIOUS_TLD_RE.search(hostname):
        if "mcp" in hostname or _MCP_SUBDOMAIN_RE.search(hostname):
            return True
    return False


def detect_shadow_patterns(url: str, hostname: str) -> Dict:
    path_match = False
    hostname_match = False
    matched_indicators = []
    confidence = "none"

    if url:
        url_lower = url.strip().lower()
        for pattern in SHADOW_PATH_PATTERNS:
            if re.search(pattern, url_lower):
                path_match = True
                matched_indicators.append(f"path:{pattern}")
        if "mcp" in url_lower and not path_match:
            for sp in SHADOW_MCP_PATHS:
                if sp in url_lower:
                    path_match = True
                    matched_indicators.append(f"known_path:{sp}")
                    break

    if hostname:
        hostname_lower = hostname.strip().lower()
        if hostname_lower in KNOWN_MCP_HOSTS:
            hostname_match = True
            matched_indicators.append(f"known_host:{hostname_lower}")
        else:
            parts = hostname_lower.split(".")
            if len(parts) >= 2:
                subdomain = parts[0]
                if _MCP_SUBDOMAIN_RE.match(subdomain):
                    hostname_match = True
                    matched_indicators.append(f"mcp_subdomain:{subdomain}")
            if not hostname_match and _SUSPICIOUS_TLD_RE.search(hostname_lower):
                if "mcp" in hostname_lower or _MCP_SUBDOMAIN_RE.search(hostname_lower):
                    hostname_match = True
                    matched_indicators.append("suspicious_tld_with_mcp")

    if path_match and hostname_match:
        confidence = "high"
    elif path_match or hostname_match:
        confidence = "medium"

    is_shadow = path_match or hostname_match

    return {
        "is_shadow_mcp": is_shadow,
        "path_match": path_match,
        "hostname_match": hostname_match,
        "matched_indicators": matched_indicators,
        "confidence": confidence,
    }


def _smoke_test():
    results = []
    errors = []

    test_cases_url = [
        ("/mcp", True),
        ("/mcp/sse", True),
        ("/api/mcp", True),
        ("/v1/mcp/stream", True),
        ("/proxy/mcp", True),
        ("/", False),
        ("/api/v2/users", False),
        ("/health", False),
        ("/mcp-exfiltrate", True),
    ]

    for path, expected in test_cases_url:
        result = is_shadow_mcp_url(path)
        status = "PASS" if result == expected else "FAIL"
        results.append(f"  is_shadow_mcp_url({path!r}) = {result}  [{status}]")
        if status == "FAIL":
            errors.append(f"Expected {expected}, got {result} for path {path!r}")

    test_cases_host = [
        ("mcp.stripe.com", True),
        ("mcp.cloudflare.com", True),
        ("mcp-github.com", True),
        ("mcp_something.example.com", True),
        ("example.com", False),
        ("api.example.com", False),
        ("www.stripe.com", False),
        ("evil-mcp.xyz", True),
    ]

    for hostname, expected in test_cases_host:
        result = is_shadow_mcp_hostname(hostname)
        status = "PASS" if result == expected else "FAIL"
        results.append(f"  is_shadow_mcp_hostname({hostname!r}) = {result}  [{status}]")
        if status == "FAIL":
            errors.append(f"Expected {expected}, got {result} for hostname {hostname!r}")

    test_cases_combined = [
        ("/mcp/sse", "mcp.stripe.com", "high"),
        ("/api/mcp", "api.example.com", "medium"),
        ("/", "mcp.cloudflare.com", "medium"),
        ("/health", "www.example.com", "none"),
        ("/mcp/exfil", "malicious-mcp.tk", "high"),
    ]

    for url, hostname, expected_conf in test_cases_combined:
        result = detect_shadow_patterns(url, hostname)
        status = "PASS" if result["confidence"] == expected_conf else "FAIL"
        results.append(
            f"  detect_shadow_patterns({url!r}, {hostname!r}) confidence={result['confidence']}  [{status}]"
        )
        if status == "FAIL":
            errors.append(
                f"Expected confidence={expected_conf}, got {result['confidence']} for url={url!r}, hostname={hostname!r}"
            )

    print("Shadow MCP Indicators Smoke Test")
    print("=" * 60)
    for r in results:
        print(r)
    print("=" * 60)

    if errors:
        print(f"FAILURES ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print(f"All tests passed ({len(results)} cases).")
        sys.exit(0)


if __name__ == "__main__":
    _smoke_test()