#!/usr/bin/env python3
"""
scanner_shadow_mcp_integration.py

Integration module that wires shadow_mcp_indicators.py into mcp_scanner.py.
Uses URL-path and hostname-pattern indicators to detect shadow MCP patterns.

Responsibilities:
1. Import detect_shadow_patterns from shadow_mcp_indicators
2. Provide scan_shadow_mcp(server_url, server_name) for mcp_scanner integration
3. Return structured detection output for registry enrichment
4. Complement body-scanning (mcp_traffic_fingerprints) - URL/hostname only

Follows Appendix B rules:
- Pure library consumption (no daemon)
- No network I/O
- No DB writes
- No import-time side effects
"""

import sys
from typing import Dict, Any, Optional
from urllib.parse import urlparse

# Import the shadow MCP detection functions
try:
    from shadow_mcp_indicators import (
        detect_shadow_patterns,
        is_shadow_mcp_url,
        is_shadow_mcp_hostname,
    )
except ImportError:
    # Fallback for standalone testing
    from unittest.mock import MagicMock
    detect_shadow_patterns = MagicMock(return_value={
        "is_shadow_mcp": False,
        "path_match": False,
        "hostname_match": False,
        "matched_indicators": [],
        "confidence": "none",
    })
    is_shadow_mcp_url = MagicMock(return_value=False)
    is_shadow_mcp_hostname = MagicMock(return_value=False)


def extract_path_from_url(url: str) -> str:
    """Extract path component from URL for URL-based detection."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        return parsed.path or ""
    except Exception:
        return ""


def extract_hostname_from_url(url: str) -> str:
    """Extract hostname from URL for hostname-based detection."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        return parsed.hostname or ""
    except Exception:
        return ""


def scan_shadow_mcp(server_url: str, server_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Scan a server URL for shadow MCP indicators.

    This is the primary integration function called by mcp_scanner during
    the scan cycle. It extracts URL path and hostname, then runs them through
    the shadow_mcp_indicators detection functions.

    Args:
        server_url: The MCP server URL to scan (e.g., "https://mcp.example.com/api")
        server_name: Optional server name for additional context

    Returns:
        Structured detection result:
        {
            "server_url": str,
            "server_name": str or None,
            "is_shadow_mcp": bool,
            "path_match": bool,
            "hostname_match": bool,
            "matched_indicators": List[str],
            "confidence": str,  # "none", "medium", or "high"
            "shadow_indicators": List[Dict]  # Detailed match info
        }
    """
    path = extract_path_from_url(server_url)
    hostname = extract_hostname_from_url(server_url)

    # Use the combined detection function from shadow_mcp_indicators
    result = detect_shadow_patterns(path, hostname)

    # Build detailed indicators list
    shadow_indicators = []
    if result.get("path_match"):
        shadow_indicators.append({
            "type": "url_path",
            "matched": path,
            "patterns": [ind for ind in result.get("matched_indicators", []) if str(ind).startswith("path:") or str(ind).startswith("known_path:")],
        })
    if result.get("hostname_match"):
        shadow_indicators.append({
            "type": "hostname",
            "matched": hostname,
            "patterns": [ind for ind in result.get("matched_indicators", []) if str(ind).startswith("known_host:") or str(ind).startswith("mcp_subdomain:") or str(ind) == "suspicious_tld_with_mcp"],
        })

    return {
        "server_url": server_url,
        "server_name": server_name,
        "is_shadow_mcp": result.get("is_shadow_mcp", False),
        "path_match": result.get("path_match", False),
        "hostname_match": result.get("hostname_match", False),
        "matched_indicators": result.get("matched_indicators", []),
        "confidence": result.get("confidence", "none"),
        "shadow_indicators": shadow_indicators,
    }


def check_url_path(url: str) -> Dict[str, Any]:
    """
    Check URL path only for shadow MCP indicators.

    Args:
        url: URL to check

    Returns:
        {
            "has_mcp_path": bool,
            "path": str,
            "matched_patterns": List[str]
        }
    """
    path = extract_path_from_url(url)
    has_mcp_path = is_shadow_mcp_url(path)

    matched = []
    if has_mcp_path:
        matched.append(path)

    return {
        "has_mcp_path": has_mcp_path,
        "path": path,
        "matched_patterns": matched,
    }


def check_hostname(url_or_hostname: str) -> Dict[str, Any]:
    """
    Check hostname only for shadow MCP indicators.

    Args:
        url_or_hostname: URL or hostname to check

    Returns:
        {
            "has_mcp_hostname": bool,
            "hostname": str,
            "matched_patterns": List[str]
        }
    """
    hostname = extract_hostname_from_url(url_or_hostname) if "://" in url_or_hostname else url_or_hostname
    has_mcp_hostname = is_shadow_mcp_hostname(hostname)

    matched = []
    if has_mcp_hostname:
        matched.append(hostname)

    return {
        "has_mcp_hostname": has_mcp_hostname,
        "hostname": hostname,
        "matched_patterns": matched,
    }


def is_likely_shadow_mcp_server(server_url: str) -> bool:
    """
    Quick boolean check if a server URL is a likely shadow MCP endpoint.

    Args:
        server_url: URL to check

    Returns:
        True if shadow MCP patterns detected, False otherwise
    """
    path = extract_path_from_url(server_url)
    hostname = extract_hostname_from_url(server_url)
    result = detect_shadow_patterns(path, hostname)
    return result.get("is_shadow_mcp", False)


# Smoke test harness
if __name__ == "__main__":
    print("=" * 60)
    print("scanner_shadow_mcp_integration - Self-Smoke Test")
    print("=" * 60)
    print()

    # Test cases: (url, server_name, expected_shadow)
    test_cases = [
        # Known MCP hosts
        ("https://mcp.stripe.com/api", "stripe-mcp", True),
        ("https://mcp.cloudflare.com/sse", "cloudflare-mcp", True),
        # MCP path patterns
        ("https://example.com/mcp", "example-mcp", True),
        ("https://example.com/api/mcp/sse", "api-mcp", True),
        ("https://example.com/v1/mcp/stream", "v1-mcp", True),
        ("https://example.com/proxy/mcp", "proxy-mcp", True),
        # Non-shadow URLs
        ("https://example.com/api/v2/users", "api-server", False),
        ("https://example.com/health", "health-check", False),
        ("https://example.com/docs", "docs", False),
        ("https://example.com/api/v1/models", "openai-proxy", False),
        # MCP subdomain patterns
        ("https://mcp-server.example.com/api", "mcp-subdomain", True),
        ("https://mcp_github.example.com", "mcp-underscore", True),
        # Suspicious TLD + MCP
        ("https://malicious-mcp.xyz/api", "malicious-tld", True),
    ]

    passed = 0
    failed = 0

    for url, name, expected_shadow in test_cases:
        result = scan_shadow_mcp(url, name)
        is_shadow = result["is_shadow_mcp"]
        status = "PASS" if is_shadow == expected_shadow else "FAIL"

        if status == "PASS":
            passed += 1
        else:
            failed += 1

        print(f"[{status}] {url}")
        print(f"  Server: {name}")
        print(f"  Shadow MCP: {is_shadow} (expected: {expected_shadow})")
        print(f"  Confidence: {result['confidence']}")
        if result['matched_indicators']:
            print(f"  Indicators: {result['matched_indicators']}")
        print()

    # Additional quick checks
    print("-" * 60)
    print("Quick Checks")
    print("-" * 60)
    print()

    quick_tests = [
        ("https://mcp.example.com/api", True),
        ("https://example.com/health", False),
        ("https://evil-mcp.tk/api", True),
    ]

    for url, expected in quick_tests:
        result = is_likely_shadow_mcp_server(url)
        status = "PASS" if result == expected else "FAIL"
        print(f"[{status}] is_likely_shadow_mcp_server({url!r}) = {result}")

    print()
    print("=" * 60)
    print(f"Smoke Test Summary: {passed} passed, {failed} failed")
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)
