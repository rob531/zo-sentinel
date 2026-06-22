#!/usr/bin/env python3
"""
scanner_traffic_fingerprints_integration.py
============================================
Integration module: wires mcp_traffic_fingerprints.py into mcp_scanner.py for
MCP protocol confirmation.  Pure library consumption – no daemon, no network, no
DB writes.  Attaches fingerprint results to scanner output for downstream
signal_analyser consumption.

Import pattern
--------------
    from scanner_traffic_fingerprints_integration import (
        enrich_scan_result,
        fingerprint_response_body,
        compose_fingerprint_blob,
    )

Public API (enricher contract)
------------------------------
    enrich_scan_result(scan_record: dict) -> dict
        Takes a raw scanner record dict (with at least 'url' and optional
        'response_body', 'headers', 'description') and returns the same dict
        augmented with fingerprint keys:

        {
            ...,
            "mcp_protocol_confirmed": bool,
            "mcp_detected_methods":  list[str],
            "mcp_fingerprint_confidence": float,   # 0.0 – 1.0
            "mcp_session_indicators": dict,
            "mcp_fingerprint_blob":   dict,         # all evidence in one place
        }

    fingerprint_response_body(body: str) -> dict
        Returns { "methods": list[str], "confidence": float }.

    compose_fingerprint_blob(methods, confidence, session_indicators) -> dict
        Assembles the evidence blob written to the record.
"""

# deps: requests
import re
import json
from typing import List, Dict, Any, Optional

# ── Import the fingerprint library ────────────────────────────────────────────
try:
    from mcp_traffic_fingerprints import (
        detect_mcp_methods,
        is_mcp_traffic,
        extract_session_indicators,
    )
except ImportError as exc:  # pragma: no cover – loaded at module definition
    raise ImportError(
        "scanner_traffic_fingerprints_integration requires mcp_traffic_fingerprints; "
        "ensure it is on the Python path."
    ) from exc

# ── Constants ──────────────────────────────────────────────────────────────────
MCP_PROTOCOL_VERSION_RE = re.compile(r'"protocolVersion"\s*:\s*"202[4-9]')
CONF_METHOD_BONUS = 0.18   # each detected MCP method adds this to confidence
CONF_BASE         = 0.25   # baseline confidence when protocol is confirmed
CONF_MAX          = 1.0

# ── Internal helpers ──────────────────────────────────────────────────────────

def _compute_confidence(methods: List[str], body: str) -> float:
    """Derive a 0.0-1.0 confidence score from detected methods and body."""
    if not methods:
        return 0.0
    raw = CONF_BASE + (len(methods) * CONF_METHOD_BONUS)
    # Penalise if protocolVersion is absent (body-level scan can miss it)
    if not MCP_PROTOCOL_VERSION_RE.search(body or ""):
        raw *= 0.85
    return round(min(CONF_MAX, raw), 3)


def _safe_str(value: Any) -> str:
    """Coerce a value to a non-null string for regex searches."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


# ── Public API ────────────────────────────────────────────────────────────────

def fingerprint_response_body(body: str) -> dict:
    """
    Scan a response body for MCP JSON-RPC method signatures.

    Returns
    -------
    dict
        {
            "methods":    list[str],   # detected MCP method names
            "confidence": float,       # 0.0 – 1.0
        }
    """
    text = _safe_str(body)
    if not text:
        return {"methods": [], "confidence": 0.0}

    if not is_mcp_traffic(text):
        return {"methods": [], "confidence": 0.0}

    methods = detect_mcp_methods(text)
    confidence = _compute_confidence(methods, text)
    return {"methods": methods, "confidence": confidence}


def compose_fingerprint_blob(
    methods: List[str],
    confidence: float,
    session_indicators: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    Assemble a canonical evidence blob from fingerprint results.

    Parameters
    ----------
    methods : list[str]
        Detected MCP method names.
    confidence : float
        Protocol confirmation confidence 0.0 – 1.0.
    session_indicators : dict, optional
        Output from extract_session_indicators.

    Returns
    -------
    dict
        {
            "confirmed":       bool,
            "methods":         list[str],
            "confidence":       float,
            "session":         dict,
            "protocol_version": str or None,
        }
    """
    blob: Dict[str, Any] = {
        "confirmed":  bool(methods),
        "methods":     list(methods),
        "confidence":  round(float(confidence), 3),
        "session":     dict(session_indicators) if session_indicators else {},
        "protocol_version": None,
    }
    return blob


def enrich_scan_result(scan_record: dict) -> dict:
    """
    Enrich a scanner output record with MCP protocol fingerprint evidence.

    This is the primary entry-point for wiring fingerprints into mcp_scanner.py.
    It reads the optional fields 'response_body' and 'headers' from the record
    (populated by the scanner's HTTP probe) and attaches fingerprint metadata.

    The enriched record is safe to pass directly to the signal_analyser for
    downstream scoring.

    Parameters
    ----------
    scan_record : dict
        A scanner output record.  Required keys: none (all optional), but
        meaningful enrichment requires 'url' at minimum.

        Expected optional keys:
            url            – server URL (used only for logging context)
            response_body  – raw HTTP response body (str)
            headers        – HTTP headers (dict or str)
            description    – package / repo description (str)

    Returns
    -------
    dict
        The input record augmented with fingerprint fields:
            mcp_protocol_confirmed       bool
            mcp_detected_methods          list[str]
            mcp_fingerprint_confidence    float (0.0 – 1.0)
            mcp_session_indicators        dict
            mcp_fingerprint_blob          dict  (all evidence in one place)

    Example
    -------
    >>> raw = {"url": "https://example.com/mcp", "response_body": '{"jsonrpc":"2.0","method":"initialize"}'}
    >>> enriched = enrich_scan_result(raw)
    >>> enriched["mcp_protocol_confirmed"]
    True
    """
    # Work on a copy so the original record is not mutated
    record = dict(scan_record)

    # --- fingerprints from body ------------------------------------------------
    body_str = _safe_str(record.get("response_body"))
    fp_body  = fingerprint_response_body(body_str)

    # --- fingerprints from description (fallback signal) --------------------
    desc_str = _safe_str(record.get("description", ""))
    fp_desc  = fingerprint_response_body(desc_str)

    # --- session / header indicators -----------------------------------------
    raw_headers = record.get("headers")
    session_indicators: Dict[str, Any] = {}
    if raw_headers is not None:
        try:
            session_indicators = extract_session_indicators(raw_headers)
        except Exception:
            session_indicators = {}

    # --- combine evidence -------------------------------------------------------
    # Prefer body results; use description only when body is empty
    if fp_body["methods"]:
        detected_methods  = fp_body["methods"]
        fingerprint_confidence = fp_body["confidence"]
    elif fp_desc["methods"]:
        detected_methods  = fp_desc["methods"]
        fingerprint_confidence = round(fp_desc["confidence"] * 0.6, 3)  # discounted
    else:
        detected_methods        = []
        fingerprint_confidence  = 0.0

    mcp_confirmed = bool(detected_methods) or session_indicators.get("is_mcp_headers", False)

    blob = compose_fingerprint_blob(
        methods=detected_methods,
        confidence=fingerprint_confidence,
        session_indicators=session_indicators,
    )

    # --- attach to record ------------------------------------------------------
    record["mcp_protocol_confirmed"]       = mcp_confirmed
    record["mcp_detected_methods"]          = detected_methods
    record["mcp_fingerprint_confidence"]   = fingerprint_confidence
    record["mcp_session_indicators"]       = session_indicators
    record["mcp_fingerprint_blob"]          = blob

    return record


# ── Self-smoke (Appendix B rule 5) ────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    cases = [
        # Case 1: explicit MCP JSON-RPC body – must confirm
        {
            "url": "https://example.com/mcp",
            "response_body": '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}',
            "headers": {"content-type": "application/json"},
            "description": "Example MCP server",
        },
        # Case 2: tools/list body
        {
            "url": "https://example.com/mcp",
            "response_body": '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}',
            "description": "MCP tools server",
        },
        # Case 3: non-MCP body – must NOT confirm
        {
            "url": "https://example.com/api",
            "response_body": '{"status":"ok","data":[]}',
            "description": "Plain REST API",
        },
        # Case 4: empty body, description-only signal
        {
            "url": "https://example.com/mcp",
            "response_body": "",
            "description": '{"jsonrpc":"2.0","method":"prompts/list"}',
        },
        # Case 5: session headers only (no body methods)
        {
            "url": "https://example.com/mcp",
            "response_body": '{"status":"ok"}',
            "headers": {
                "mcp-session-id": "sess_abc123def456",
                "x-mcp-protocol-version": "2024-11-05",
            },
        },
    ]

    expected_confirmed = [True, True, False, True, True]
    passed = 0

    for i, case in enumerate(cases, 1):
        enriched = enrich_scan_result(case)
        confirmed = enriched["mcp_protocol_confirmed"]
        expected  = expected_confirmed[i - 1]
        ok = confirmed == expected
        status = "PASS" if ok else "FAIL"
        print(
            f"[{status}] case {i}: confirmed={confirmed} "
            f"(expected={expected})  methods={enriched['mcp_detected_methods']}  "
            f"conf={enriched['mcp_fingerprint_confidence']}"
        )
        if ok:
            passed += 1

    print(f"\n{passed}/{len(cases)} smoke tests passed")
    if passed < len(cases):
        print("SOME SMOKE TESTS FAILED", file=sys.stderr)
        sys.exit(1)
    else:
        print("All smoke tests PASSED")
        sys.exit(0)
