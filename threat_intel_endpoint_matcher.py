"""
threat_intel_endpoint_matcher.py

Pure library for matching endpoint hostnames against threat intelligence feeds.
Part of Phase IV dominant negative-signal stack per ZO-SENTINEL spec section Appendix C.

Design principles (per Phase IV spec):
- Negative signals dominate positive: any match = block.
- Pure library: no I/O, no network, no DB, no protected imports.
- Stdlib only (re module is the primary tool).
- Deterministic: first match wins.
"""

import re
from typing import Optional, Dict, List, Union

__all__ = ["match_endpoints"]


# Pre-compiled regex to extract hostname from a URL-like string.
# Captures the host portion of scheme://host[:port][/path][?query][#fragment]
_URL_HOST_RE = re.compile(
    r'^[a-zA-Z][a-zA-Z0-9+.\-]*://([^/:?#]+)',
    re.IGNORECASE,
)


def _extract_hostname(endpoint: str) -> str:
    """
    Extract a normalized (lowercase) hostname from a URL or bare hostname.

    Handles:
      - Bare hostnames:        "evil.example.com"
      - URLs with scheme:      "https://evil.example.com/path"
      - URLs with port:        "https://evil.example.com:8443/x"
      - Bare host with port:   "evil.example.com:8080"
    Returns "" for unparseable input.
    """
    if not isinstance(endpoint, str) or not endpoint:
        return ""

    endpoint = endpoint.strip()
    if not endpoint:
        return ""

    # URL with explicit scheme -> use the URL regex.
    m = _URL_HOST_RE.match(endpoint)
    if m:
        return m.group(1).lower()

    # Bare host (possibly with port/path/query/fragment) -> strip everything
    # after the first delimiter that is not part of a hostname.
    host = re.split(r'[/?#]', endpoint, maxsplit=1)[0]
    # Strip port if present.
    host = host.split(':', 1)[0]
    return host.lower()


def match_endpoints(
    endpoints: Optional[List[Union[str, bytes]]],
    feeds: Dict[str, List[str]],
) -> Optional[Dict[str, Union[bool, str]]]:
    """
    Match endpoint hostnames against threat intelligence feeds.

    Parameters
    ----------
    endpoints : list
        Endpoint strings (hostnames or URLs) to evaluate. Bytes/None tolerated.
    feeds : dict
        Mapping of feed_name -> list of regex patterns (strings).
        A pattern match (re.search) is case-insensitive.

    Returns
    -------
    dict or None
        On any match: {"blocked": True, "feed": <feed_name>, "hostname": <host>}.
        On no match:  None.
        First match wins (deterministic, dominant negative-signal semantics).
    """
    if not endpoints or not feeds:
        return None

    for endpoint in endpoints:
        if endpoint is None:
            continue
        # Coerce bytes to str defensively.
        if isinstance(endpoint, bytes):
            try:
                endpoint = endpoint.decode("utf-8", errors="replace")
            except Exception:
                continue

        hostname = _extract_hostname(endpoint)
        if not hostname:
            continue

        for feed_name, patterns in feeds.items():
            if not patterns:
                continue
            for pattern in patterns:
                if not isinstance(pattern, str) or not pattern:
                    continue
                try:
                    if re.search(pattern, hostname, re.IGNORECASE):
                        return {
                            "blocked": True,
                            "feed": feed_name,
                            "hostname": hostname,
                        }
                except re.error:
                    # Skip malformed patterns silently; matcher must not crash.
                    continue

    return None


# ---------------------------------------------------------------------------
# Self-test (run only when executed directly)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Synthetic threat-intel feeds.
    synthetic_feeds = {
        "malware_c2": [
            r"^malicious\.example\.com$",
            r".*\.evil\.net$",
            r"^c2-[0-9]+\.badactor\.org$",
        ],
        "phishing": [
            r"^secure-login-.*\.phish\.com$",
            r".*paypal-.*\.tk$",
        ],
        "ransomware": [
            r".*\.locky\..*",
        ],
    }

    # Test 1: known-bad direct hostname match.
    r1 = match_endpoints(
        ["google.com", "malicious.example.com", "github.com"],
        synthetic_feeds,
    )
    assert r1 is not None, "Test 1 failed: no match for malicious.example.com"
    assert r1 == {
        "blocked": True,
        "feed": "malware_c2",
        "hostname": "malicious.example.com",
    }, f"Test 1 failed: unexpected payload {r1!r}"
    print("[OK] Test 1: direct known-bad hostname match detected")

    # Test 2: URL with scheme/path/port.
    r2 = match_endpoints(
        ["https://safe.com/page", "http://api.evil.net:8080/v1/data"],
        synthetic_feeds,
    )
    assert r2 is not None, "Test 2 failed: no match for evil.net URL"
    assert r2["hostname"] == "api.evil.net", f"Test 2 failed: host {r2['hostname']!r}"
    assert r2["feed"] == "malware_c2", f"Test 2 failed: feed {r2['feed']!r}"
    assert r2["blocked"] is True, "Test 2 failed: blocked not True"
    print("[OK] Test 2: URL extraction (scheme + port + path)")

    # Test 3: phishing feed match.
    r3 = match_endpoints(
        ["https://secure-login-update.phish.com/auth"],
        synthetic_feeds,
    )
    assert r3 is not None, "Test 3 failed: phishing URL not flagged"
    assert r3["feed"] == "phishing", f"Test 3 failed: feed {r3['feed']!r}"
    print("[OK] Test 3: phishing feed match")

    # Test 4: clean endpoints -> no match.
    r4 = match_endpoints(
        ["google.com", "github.com", "https://stackoverflow.com/questions"],
        synthetic_feeds,
    )
    assert r4 is None, f"Test 4 failed: clean endpoints matched {r4!r}"
    print("[OK] Test 4: clean endpoints return None")

    # Test 5: empty / None / malformed inputs are safe.
    assert match_endpoints([], synthetic_feeds) is None, "Test 5a failed"
    assert match_endpoints(["any.com"], {}) is None, "Test 5b failed"
    assert match_endpoints(None, synthetic_feeds) is None, "Test 5c failed"
    assert match_endpoints([""], synthetic_feeds) is None, "Test 5d failed"
    assert match_endpoints([None], synthetic_feeds) is None, "Test 5e failed"
    assert match_endpoints(["evil.com"], {"f": ["[invalid-regex"]}) is None, (
        "Test 5f failed: malformed pattern should be skipped, not crash"
    )
    print("[OK] Test 5: empty/None/malformed input handling")

    # Test 6: case-insensitivity (negative-signal: any case variant matches).
    r6 = match_endpoints(["MaLiCiOuS.ExAmPlE.cOm"], synthetic_feeds)
    assert r6 is not None, "Test 6 failed: case variant not flagged"
    assert r6["hostname"] == "malicious.example.com", (
        f"Test 6 failed: host normalization {r6['hostname']!r}"
    )
    print("[OK] Test 6: case-insensitive match with host normalization")

    # Test 7: ransomware feed match.
    r7 = match_endpoints(["https://payload.locky.crypto/files"], synthetic_feeds)
    assert r7 is not None, "Test 7 failed: ransomware not flagged"
    assert r7["feed"] == "ransomware", f"Test 7 failed: feed {r7['feed']!r}"
    print("[OK] Test 7: ransomware feed match")

    # Test 8: first match wins (deterministic, dominant negative signal).
    # Even though a later feed could also match, the first one is returned.
    overlapping_feeds = {
        "first_feed": [r"^evil\.test\.com$"],
        "second_feed": [r"^evil\.test\.com$"],
    }
    r8 = match_endpoints(["evil.test.com"], overlapping_feeds)
    assert r8 is not None, "Test 8 failed"
    assert r8["feed"] == "first_feed", (
        f"Test 8 failed: expected first_feed, got {r8['feed']!r}"
    )
    print("[OK] Test 8: deterministic first-match-wins ordering")

    print("\nAll self-tests passed. Phase IV threat intel matcher operational.")