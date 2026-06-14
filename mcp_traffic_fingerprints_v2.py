"""
Cloudflare enterprise MCP reference architecture, blog.cloudflare.com/enterprise-mcp, 2026-04-14.

MCP traffic fingerprint detection library for identifying Model Context Protocol
traffic patterns in raw text streams. Pure stdlib implementation with compiled
regex patterns at module load time.
"""

import re
from typing import Dict, List

# =============================================================================
# Compiled Regex Patterns (module load time)
# =============================================================================

# JSON-RPC method extraction pattern
_METHOD_PATTERN: re.Pattern = re.compile(
    r'"method"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"'
)

# MCP protocol version marker
_PROTOCOL_VERSION_PATTERN: re.Pattern = re.compile(
    r'"protocolVersion"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"'
)

# JSON-RPC 2.0 version identifier
_JSONRPC_VERSION_PATTERN: re.Pattern = re.compile(
    r'"jsonrpc"\s*:\s*"2\.0"'
)

# MCP session token patterns
_SESSION_TOKEN_PATTERN: re.Pattern = re.compile(
    r'(?:mcp_session|session_token|mcp_token|auth_token)\s*[:=]\s*["\']?([a-zA-Z0-9_=\-+/]{16,})["\']?',
    re.IGNORECASE
)

# MCP session cookie patterns
_SESSION_COOKIE_PATTERN: re.Pattern = re.compile(
    r'Cookie\s*:\s*(?:mcp_session|session|mcp_token)=([^;,\s]+)',
    re.IGNORECASE
)

# MCP endpoint URI patterns
_ENDPOINT_PATTERN: re.Pattern = re.compile(
    r'(?:https?://)?(?:api\.mcp|modelcontextprotocol)\.[a-z]{2,}(?:/[\w\-./?%&=]*)?',
    re.IGNORECASE
)

# Known MCP JSON-RPC method names (Appendix B reference)
_MCP_KNOWN_METHODS: frozenset = frozenset({
    "tools/call",
    "tools/list",
    "tools/list_changed",
    "resources/list",
    "resources/read",
    "resources/subscribe",
    "resources/unsubscribe",
    "prompts/list",
    "prompts/get",
    "prompts/create",
    "prompts/update",
    "prompts/delete",
    "roots/list",
    "roots/add",
    "roots/remove",
    "sampling/create_message",
    "logging/set_level",
    "logging/emits",
    "initialize",
    "shutdown",
    "ping",
    "notifications/initialized",
    "notifications/tools/list_changed",
    "notifications/resources/list_changed",
    "notifications/roots/list_changed",
    "notifications/prompts/list_changed",
})


# =============================================================================
# Core Detection Functions
# =============================================================================

def detect_mcp_methods(text: str) -> List[str]:
    """
    Detect MCP JSON-RPC method names in raw text.
    
    Args:
        text: Raw text input to scan for MCP method names.
        
    Returns:
        List of matched MCP method name strings (unique, order-preserving).
        Returns empty list if no MCP methods detected.
    """
    if not isinstance(text, str) or not text:
        return []
    
    methods: List[str] = []
    seen: set = set()
    
    for match in _METHOD_PATTERN.finditer(text):
        method = match.group(1).strip()
        if method and method not in seen:
            seen.add(method)
            methods.append(method)
    
    return methods


def is_mcp_traffic(text: str) -> bool:
    """
    Determine if text contains MCP protocol traffic.
    
    Detection logic:
    1. Must contain JSON-RPC 2.0 marker OR protocolVersion field
    2. Must contain a known MCP method OR explicit protocolVersion
    
    Args:
        text: Raw text input to analyze.
        
    Returns:
        True if MCP traffic detected, False otherwise.
    """
    if not isinstance(text, str) or not text:
        return False
    
    # Check for JSON-RPC 2.0 version marker
    has_jsonrpc_marker = _JSONRPC_VERSION_PATTERN.search(text) is not None
    
    # Check for MCP protocol version
    has_protocol_version = _PROTOCOL_VERSION_PATTERN.search(text) is not None
    
    # Check for known MCP methods
    detected_methods = detect_mcp_methods(text)
    has_known_method = any(
        method in _MCP_KNOWN_METHODS for method in detected_methods
    )
    
    # MCP traffic requires either:
    # - JSON-RPC 2.0 marker + known MCP method
    # - Explicit protocolVersion field
    if has_protocol_version:
        return True
    
    if has_jsonrpc_marker and has_known_method:
        return True
    
    # Fallback: check for MCP endpoint patterns
    if _ENDPOINT_PATTERN.search(text) and detected_methods:
        return True
    
    return False


def extract_session_indicators(text: str) -> Dict[str, str]:
    """
    Extract MCP session markers from raw text.
    
    Args:
        text: Raw text input to scan for session indicators.
        
    Returns:
        Dict with keys:
        - 'session_token': MCP session token value if found
        - 'session_cookie': MCP session cookie value if found
        - 'endpoint': MCP endpoint URI if found
        Empty dict if no session indicators detected.
    """
    if not isinstance(text, str) or not text:
        return {}
    
    indicators: Dict[str, str] = {}
    
    # Extract session token
    token_match = _SESSION_TOKEN_PATTERN.search(text)
    if token_match:
        indicators["session_token"] = token_match.group(1)
    
    # Extract session cookie
    cookie_match = _SESSION_COOKIE_PATTERN.search(text)
    if cookie_match:
        indicators["session_cookie"] = cookie_match.group(1)
    
    # Extract MCP endpoint
    endpoint_match = _ENDPOINT_PATTERN.search(text)
    if endpoint_match:
        indicators["endpoint"] = endpoint_match.group(0)
    
    return indicators


# =============================================================================
# Acceptance Tests
# =============================================================================

if __name__ == "__main__":
    import sys
    
    test_results: List[tuple] = []
    
    # Test 1: JSON-RPC request with tools/call method
    test_input_1 = '{"method":"tools/call","params":{}}'
    result_1_methods = detect_mcp_methods(test_input_1)
    result_1_traffic = is_mcp_traffic(test_input_1)
    result_1_session = extract_session_indicators(test_input_1)
    
    assert result_1_traffic is True, (
        f"Test 1 FAILED: is_mcp_traffic returned {result_1_traffic}, expected True"
    )
    assert "tools/call" in result_1_methods, (
        f"Test 1 FAILED: tools/call not in {result_1_methods}"
    )
    test_results.append(("Test 1 (tools/call)", True))
    
    # Test 2: Protocol version marker (no method)
    test_input_2 = '{"protocolVersion":"2024-11-05"}'
    result_2_methods = detect_mcp_methods(test_input_2)
    result_2_traffic = is_mcp_traffic(test_input_2)
    result_2_session = extract_session_indicators(test_input_2)
    
    assert result_2_traffic is True, (
        f"Test 2 FAILED: is_mcp_traffic returned {result_2_traffic}, expected True"
    )
    assert result_2_methods == [], (
        f"Test 2 FAILED: expected empty methods, got {result_2_methods}"
    )
    test_results.append(("Test 2 (protocolVersion)", True))
    
    # Test 3: Plain text with no MCP content
    test_input_3 = "This is plain text with no MCP protocol content whatsoever."
    result_3_methods = detect_mcp_methods(test_input_3)
    result_3_traffic = is_mcp_traffic(test_input_3)
    result_3_session = extract_session_indicators(test_input_3)
    
    assert result_3_traffic is False, (
        f"Test 3 FAILED: is_mcp_traffic returned {result_3_traffic}, expected False"
    )
    assert result_3_methods == [], (
        f"Test 3 FAILED: expected empty methods, got {result_3_methods}"
    )
    assert result_3_session == {}, (
        f"Test 3 FAILED: expected empty session indicators, got {result_3_session}"
    )
    test_results.append(("Test 3 (plain text)", True))
    
    # Test 4: Complete MCP request with session
    test_input_4 = (
        '{"jsonrpc":"2.0","id":1,"method":"resources/list",'
        '"params":{}}'
    )
    result_4_methods = detect_mcp_methods(test_input_4)
    result_4_traffic = is_mcp_traffic(test_input_4)
    
    assert result_4_traffic is True, (
        f"Test 4 FAILED: is_mcp_traffic returned {result_4_traffic}, expected True"
    )
    assert "resources/list" in result_4_methods, (
        f"Test 4 FAILED: resources/list not in {result_4_methods}"
    )
    test_results.append(("Test 4 (resources/list)", True))
    
    # Test 5: Empty input handling
    test_input_5 = ""
    result_5_methods = detect_mcp_methods(test_input_5)
    result_5_traffic = is_mcp_traffic(test_input_5)
    result_5_session = extract_session_indicators(test_input_5)
    
    assert result_5_methods == [], (
        f"Test 5 FAILED: expected empty methods for empty input"
    )
    assert result_5_traffic is False, (
        f"Test 5 FAILED: expected False for empty input"
    )
    assert result_5_session == {}, (
        f"Test 5 FAILED: expected empty session for empty input"
    )
    test_results.append(("Test 5 (empty input)", True))
    
    # Print results
    print("=" * 60)
    print("MCP Traffic Fingerprints v2 - Acceptance Tests")
    print("=" * 60)
    for name, passed in test_results:
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
    print("=" * 60)
    print(f"All {len(test_results)} tests passed.")
    print("Gate 8 compliance verified. Exiting with code 0.")
    sys.exit(0)