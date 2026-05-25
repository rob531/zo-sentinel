import re
import json
from typing import List, Dict, Any, Optional

SERVICE_NAME = "mcp_traffic_fingerprints"
LOG_FILE = "/home/workspace/logs/mcp_traffic_fingerprints.log"

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(SERVICE_NAME)

MCP_METHODS = [
    "initialize",
    "tools/call",
    "tools/list",
    "resources/read",
    "resources/list",
    "prompts/list",
    "prompts/get",
    "sampling/createMessage",
    "notifications/initialized",
    "roots/list",
]

_INITIALIZE_PATTERN = re.compile(
    r'"method"\s*:\s*"initialize"',
    flags=re.IGNORECASE
)

_TOOLS_CALL_PATTERN = re.compile(
    r'"method"\s*:\s*"tools/call"',
    flags=re.IGNORECASE
)

_TOOLS_LIST_PATTERN = re.compile(
    r'"method"\s*:\s*"tools/list"',
    flags=re.IGNORECASE
)

_RESOURCES_READ_PATTERN = re.compile(
    r'"method"\s*:\s*"resources/read"',
    flags=re.IGNORECASE
)

_RESOURCES_LIST_PATTERN = re.compile(
    r'"method"\s*:\s*"resources/list"',
    flags=re.IGNORECASE
)

_PROMPTS_LIST_PATTERN = re.compile(
    r'"method"\s*:\s*"prompts/list"',
    flags=re.IGNORECASE
)

_PROMPTS_GET_PATTERN = re.compile(
    r'"method"\s*:\s*"prompts/get"',
    flags=re.IGNORECASE
)

_SAMPLING_CREATE_MESSAGE_PATTERN = re.compile(
    r'"method"\s*:\s*"sampling/createMessage"',
    flags=re.IGNORECASE
)

_NOTIFICATIONS_INITIALIZED_PATTERN = re.compile(
    r'"method"\s*:\s*"notifications/initialized"',
    flags=re.IGNORECASE
)

_ROOTS_LIST_PATTERN = re.compile(
    r'"method"\s*:\s*"roots/list"',
    flags=re.IGNORECASE
)

_METHOD_PATTERNS = {
    "initialize": _INITIALIZE_PATTERN,
    "tools/call": _TOOLS_CALL_PATTERN,
    "tools/list": _TOOLS_LIST_PATTERN,
    "resources/read": _RESOURCES_READ_PATTERN,
    "resources/list": _RESOURCES_LIST_PATTERN,
    "prompts/list": _PROMPTS_LIST_PATTERN,
    "prompts/get": _PROMPTS_GET_PATTERN,
    "sampling/createMessage": _SAMPLING_CREATE_MESSAGE_PATTERN,
    "notifications/initialized": _NOTIFICATIONS_INITIALIZED_PATTERN,
    "roots/list": _ROOTS_LIST_PATTERN,
}

_PROTOCOL_VERSION_PATTERN = re.compile(r'"protocolVersion"\s*:\s*"202[4-9]')

_JSONRPC_VERSION_PATTERN = re.compile(
    r'"jsonrpc"\s*:\s*"2\.0"',
    flags=re.IGNORECASE
)

_MCP_REQUEST_ID_PATTERN = re.compile(
    r'"id"\s*:\s*(?:"([^"]+)"|(\d+))',
    flags=re.IGNORECASE
)

_MCP_SESSION_ID_PATTERN = re.compile(
    r'"sessionId"\s*:\s*"([^"]{8,})"',
    flags=re.IGNORECASE
)

_MCP_ENDPOINT_HEADER_PATTERN = re.compile(
    r"(?i)(mcp|x-mcp|mcp-server|modelcontextprotocol)",
    flags=re.IGNORECASE
)

def detect_mcp_methods(text: str) -> List[str]:
    """
    Scan text for known MCP JSON-RPC method names.
    Returns list of matched method names from MCP_METHODS.
    """
    if not isinstance(text, str):
        return []
    detected = []
    for method, pattern in _METHOD_PATTERNS.items():
        if pattern.search(text):
            detected.append(method)
    return detected


def has_jsonrpc_version(text: str) -> bool:
    """Check if text contains a jsonrpc 2.0 version marker."""
    if not isinstance(text, str):
        return False
    return bool(_JSONRPC_VERSION_PATTERN.search(text))


def has_protocol_version(text: str) -> bool:
    """Check if text contains a protocol version in range 2024-2029."""
    if not isinstance(text, str):
        return False
    return bool(_PROTOCOL_VERSION_PATTERN.search(text))


def is_mcp_traffic(payload: Any) -> bool:
    """
    Determine if the given payload appears to be MCP JSON-RPC traffic.
    Accepts a string, dict, or bytes. Returns True if the payload
    exhibits MCP characteristics (jsonrpc version + known method or protocolVersion).
    """
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8", errors="ignore")
        except Exception:
            return False

    if isinstance(payload, dict):
        try:
            payload = json.dumps(payload)
        except Exception:
            return False
    elif not isinstance(payload, str):
        return False

    if not has_jsonrpc_version(payload):
        return False

    has_method = detect_mcp_methods(payload)
    has_version = has_protocol_version(payload)

    if has_method or has_version:
        return True

    return False


def extract_session_indicators(headers: Any) -> Dict[str, Any]:
    """
    Extract MCP session indicators from HTTP headers dict or raw header string.
    Returns a dict with keys: has_session_id, session_id, is_mcp_headers,
    detected_header_names, has_request_id.
    """
    result = {
        "has_session_id": False,
        "session_id": None,
        "is_mcp_headers": False,
        "detected_header_names": [],
        "has_request_id": False,
    }

    header_map: Dict[str, str] = {}

    if isinstance(headers, dict):
        for k, v in headers.items():
            header_map[str(k).lower()] = str(v).lower()
    elif isinstance(headers, str):
        for line in headers.split("\n"):
            if ":" in line:
                key, _, val = line.partition(":")
                header_map[key.strip().lower()] = val.strip().lower()
    elif isinstance(headers, (list, tuple)):
        for item in headers:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                header_map[str(item[0]).lower()] = str(item[1]).lower()
            elif isinstance(item, str) and ":" in item:
                key, _, val = item.partition(":")
                header_map[key.strip().lower()] = val.strip().lower()

    mcp_related_headers = [
        "mcp-session-id",
        "x-mcp-session-id",
        "mcp-server-session",
        "x-mcp-server-session",
        "mcp-protocol-version",
        "x-mcp-protocol-version",
        "mcp-endpoint",
        "x-mcp-endpoint",
    ]

    for header_name in header_map:
        if any(mcp_h in header_name for mcp_h in mcp_related_headers):
            result["detected_header_names"].append(header_name)
            result["is_mcp_headers"] = True

    for header_name, header_value in header_map.items():
        sid_match = re.search(r"([^;,\s]{8,})", header_value)
        if sid_match and "session" in header_name:
            result["has_session_id"] = True
            result["session_id"] = sid_match.group(1)
            break

    if "mcp-session-id" in header_map or "x-mcp-session-id" in header_map:
        result["has_session_id"] = True
        for h in ["mcp-session-id", "x-mcp-session-id"]:
            if h in header_map:
                result["session_id"] = header_map[h]
                break

    for header_value in header_map.values():
        if _MCP_REQUEST_ID_PATTERN.search(header_value):
            result["has_request_id"] = True
            break

    return result


def has_mcp_endpoint_indicator(text: str) -> bool:
    """Check if text contains an MCP endpoint indicator."""
    if not isinstance(text, str):
        return False
    endpoint_patterns = [
        r"/mcp",
        r"/modelcontextprotocol",
        r"mcp\.jsonrpc",
        r'"method"\s*:\s*"[^"]+"',
    ]
    for pattern in endpoint_patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True
    return False


if __name__ == "__main__":
    log.info("Starting self-smoke tests for mcp_traffic_fingerprints")

    test_cases = [
        {
            "name": "initialize_request",
            "input": '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}',
            "expected_methods": ["initialize"],
            "expect_mcp": True,
        },
        {
            "name": "tools_list_and_call",
            "input": '{"jsonrpc": "2.0","id":42,"method":"tools/list"}\n{"jsonrpc": "2.0","id":43,"method":"tools/call","params":{"name":"read_file"}}',
            "expected_methods": ["tools/list", "tools/call"],
            "expect_mcp": True,
        },
        {
            "name": "session_indicator_headers",
            "input": {
                "mcp-session-id": "sess_a1b2c3d4e5f6",
                "content-type": "application/json",
                "x-mcp-protocol-version": "2024-11-05"
            },
            "expected_has_session": True,
            "expected_is_mcp_headers": True,
        },
    ]

    passed = 0
    failed = 0

    for tc in test_cases:
        name = tc["name"]
        log.info(f"  Running: {name}")

        if "expected_methods" in tc:
            input_text = tc["input"]
            detected = detect_mcp_methods(input_text)
            expected = tc["expected_methods"]
            if set(detected) == set(expected):
                log.info(f"    PASS detect_mcp_methods: got {detected}")
                passed += 1
            else:
                log.error(f"    FAIL detect_mcp_methods: expected {expected}, got {detected}")
                failed += 1

            mcp_ok = is_mcp_traffic(input_text)
            if mcp_ok == tc["expect_mcp"]:
                log.info(f"    PASS is_mcp_traffic: got {mcp_ok}")
                passed += 1
            else:
                log.error(f"    FAIL is_mcp_traffic: expected {tc['expect_mcp']}, got {mcp_ok}")
                failed += 1

        if "expected_has_session" in tc:
            indicators = extract_session_indicators(tc["input"])
            if indicators.get("has_session_id") == tc["expected_has_session"]:
                log.info(f"    PASS has_session_id: got {indicators.get('has_session_id')}")
                passed += 1
            else:
                log.error(f"    FAIL has_session_id: expected {tc['expected_has_session']}, got {indicators.get('has_session_id')}")
                failed += 1

            if indicators.get("is_mcp_headers") == tc["expected_is_mcp_headers"]:
                log.info(f"    PASS is_mcp_headers: got {indicators.get('is_mcp_headers')}")
                passed += 1
            else:
                log.error(f"    FAIL is_mcp_headers: expected {tc['expected_is_mcp_headers']}, got {indicators.get('is_mcp_headers')}")
                failed += 1

    log.info(f"Smoke complete: {passed} passed, {failed} failed")
    if failed > 0:
        log.error("Some smoke tests FAILED")
        exit(1)
    else:
        log.info("All smoke tests PASSED")
        exit(0)