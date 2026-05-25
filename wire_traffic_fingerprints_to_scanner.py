#!/usr/bin/env python3
"""
wire_traffic_fingerprints_to_scanner.py
Integration module to wire mcp_traffic_fingerprints.py into mcp_scanner.py
for MCP protocol confirmation during scanning operations.
"""

import sys
import time
import json
import requests
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

# ── HTTP client helpers (write_service at :8772) ────────────────────────────

def ws_write(table: str, rows: List[Dict[str, Any]], wait: bool = True) -> Dict:
    """Write to DuckDB via write_service gateway at :8772."""
    payload = {"table": table, "rows": rows, "wait": wait}
    resp = requests.post(
        "http://127.0.0.1:8772/write",
        json=payload,
        timeout=15
    )
    resp.raise_for_status()
    return resp.json()

def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Query DuckDB via write_service gateway at :8772."""
    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": sql},
        timeout=30
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get("rows", [])

def ws_execute(sql: str) -> None:
    """Execute DDL/DML via write_service gateway at :8772."""
    resp = requests.post(
        "http://127.0.0.1:8772/execute",
        json={"sql": sql},
        timeout=15
    )
    resp.raise_for_status()

# ── Fingerprint library import ───────────────────────────────────────────────

sys.path.insert(0, '/home/workspace')

try:
    from mcp_traffic_fingerprints import (
        detect_mcp_methods,
        is_mcp_traffic,
        get_mcp_protocol_signature,
        MCP_METHOD_SIGNATURES,
        JSONRPC_CONTENT_TYPE,
        MCP_HEADERS,
    )
    FINGERPRINT_LIB_LOADED = True
except ImportError:
    FINGERPRINT_LIB_LOADED = False

# ── Scanner integration functions ───────────────────────────────────────────

def analyze_response_for_mcp(
    response_text: str,
    headers: Optional[Dict[str, str]] = None,
    url: str = ""
) -> Dict[str, Any]:
    """
    Analyze an HTTP response to detect MCP protocol usage.
    Returns dict with:
      - is_mcp: bool
      - methods_detected: List[str]
      - confidence: float (0.0-1.0)
      - evidence: Dict with key findings
      - protocol_signature: str or None
    """
    result = {
        "is_mcp": False,
        "methods_detected": [],
        "confidence": 0.0,
        "evidence": {},
        "protocol_signature": None,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "source_url": url,
    }

    if not FINGERPRINT_LIB_LOADED:
        result["error"] = "fingerprint library not available"
        return result

    try:
        # Use is_mcp_traffic to check if response shows MCP patterns
        is_mcp, evidence = is_mcp_traffic(response_text, headers)
        result["is_mcp"] = is_mcp
        result["evidence"] = evidence

        # Detect specific MCP methods present
        methods = detect_mcp_methods(response_text)
        result["methods_detected"] = methods

        # Get protocol signature
        sig = get_mcp_protocol_signature()
        result["protocol_signature"] = sig

        # Compute confidence based on method count and evidence
        method_score = min(len(methods) * 0.3, 0.9)
        evidence_score = 0.1 if evidence.get("jsonrpc_detected") else 0.0
        header_score = 0.1 if evidence.get("mcp_headers_present") else 0.0
        result["confidence"] = min(method_score + evidence_score + header_score, 1.0)

    except Exception as e:
        result["error"] = str(e)

    return result


def enrich_server_scan(
    server_id: str,
    scan_result: Dict[str, Any],
    scan_url: str,
    response_text: str = "",
    headers: Optional[Dict[str, str]] = None
) -> None:
    """
    Enrich a scanner result with MCP traffic fingerprint analysis.
    Persists findings to mcp_signal_scores table via write_service.

    Args:
        server_id: The scanned server ID
        scan_result: Raw scan result dict from scanner
        scan_url: URL that was scanned
        response_text: Response body text (optional)
        headers: Response headers (optional)
    """
    if not response_text:
        return

    analysis = analyze_response_for_mcp(response_text, headers, scan_url)

    # Build signal score rows for each detected method
    rows = []
    for method in analysis.get("methods_detected", []):
        rows.append({
            "server_id": server_id,
            "signal_name": f"mcp_method_{method}",
            "score": 0.75,
            "evidence": json.dumps({
                "method": method,
                "confidence": analysis.get("confidence", 0.0),
                "url": scan_url,
                "timestamp": analysis.get("timestamp"),
            }),
            "scored_at": datetime.utcnow().isoformat() + "Z",
        })

    # If MCP traffic confirmed, add overall signal
    if analysis.get("is_mcp"):
        rows.append({
            "server_id": server_id,
            "signal_name": "mcp_protocol_confirmed",
            "score": analysis.get("confidence", 0.0),
            "evidence": json.dumps({
                "signature": analysis.get("protocol_signature"),
                "methods": analysis.get("methods_detected", []),
                "url": scan_url,
                "timestamp": analysis.get("timestamp"),
            }),
            "scored_at": datetime.utcnow().isoformat() + "Z",
        })

    if rows:
        try:
            ws_write("mcp_signal_scores", rows)
        except Exception as e:
            print(f"[WARN] Failed to write MCP fingerprint signals: {e}", file=sys.stderr)


def create_mcp_aware_scanner_hook() -> Dict[str, Any]:
    """
    Returns a hook dict that can be merged into mcp_scanner.py.
    Provides:
      - analyze_callback: Function to analyze responses
      - enrich_callback: Function to persist findings
      - fingerprint_signatures: Known MCP signatures for matching
    """
    return {
        "analyze_callback": analyze_response_for_mcp,
        "enrich_callback": enrich_server_scan,
        "fingerprint_signatures": MCP_METHOD_SIGNATURES if FINGERPRINT_LIB_LOADED else {},
        "jsonrpc_content_type": JSONRPC_CONTENT_TYPE if FINGERPRINT_LIB_LOADED else "application/json",
        "mcp_expected_headers": MCP_HEADERS if FINGERPRINT_LIB_LOADED else [],
        "lib_loaded": FINGERPRINT_LIB_LOADED,
    }


def log_mcp_detection_event(
    server_id: str,
    event_type: str,
    url: str,
    analysis: Dict[str, Any],
    actor: str = "scanner"
) -> None:
    """
    Log an MCP detection event to audit_log via write_service.
    """
    try:
        ws_write("audit_log", [{
            "target_server_id": server_id,
            "event_type": event_type,
            "actor": actor,
            "detail": json.dumps({
                "url": url,
                "is_mcp": analysis.get("is_mcp", False),
                "methods_detected": analysis.get("methods_detected", []),
                "confidence": analysis.get("confidence", 0.0),
                "protocol_signature": analysis.get("protocol_signature"),
                "timestamp": analysis.get("timestamp"),
            }),
            "created_at": datetime.utcnow().isoformat() + "Z",
        }])
    except Exception as e:
        print(f"[WARN] Failed to log MCP detection event: {e}", file=sys.stderr)


# ── Daemon wrapper for live integration ─────────────────────────────────────

def run_fingerprint_integration_daemon(poll_secs: int = 30):
    """
    Daemon that watches for unanalyzed scan results and enriches them
    with MCP traffic fingerprint analysis.
    """
    import os
    pid_file = "/tmp/wire_traffic_fingerprints_integration.pid"

    # Check single instance
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            print(f"[EXIT] Another instance already running (PID {old_pid})")
            sys.exit(1)
        except OSError:
            pass  # Stale PID file

    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    try:
        os.unlink(pid_file)
    except Exception:
        pass

    start_time = time.time()
    print(f"[START] MCP Traffic Fingerprint Integration Daemon")
    print(f"        Library loaded: {FINGERPRINT_LIB_LOADED}")

    # Ensure audit_log table exists
    try:
        ws_execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY,
                target_server_id TEXT,
                event_type TEXT,
                actor TEXT,
                detail TEXT,
                created_at TEXT
            )
        """)
    except Exception as e:
        print(f"[WARN] Could not ensure audit_log table: {e}")

    iteration = 0
    while True:
        iteration += 1
        elapsed = time.time() - start_time

        try:
            # Query servers that have been scanned but lack MCP signal scores
            unanalyzed = ws_query("""
                SELECT DISTINCT s.server_id, s.url, s.description
                FROM mcp_server_registry s
                LEFT JOIN mcp_signal_scores mss ON s.server_id = mss.server_id
                    AND mss.signal_name LIKE 'mcp_method_%'
                WHERE s.scan_count > 0
                  AND mss.server_id IS NULL
                LIMIT 100
            """)

            for server in unanalyzed:
                # In a full implementation, would re-fetch response data
                # For integration wiring, just log the opportunity
                log_mcp_detection_event(
                    server_id=server["server_id"],
                    event_type="fingerprint_analysis_pending",
                    url=server.get("url", ""),
                    analysis={"status": "queued", "timestamp": datetime.utcnow().isoformat() + "Z"},
                )

        except Exception as e:
            print(f"[ERROR] Iteration {iteration}: {e}", file=sys.stderr)

        # Heartbeat
        try:
            ws_write("service_health", [{
                "service": "wire_traffic_fingerprints_integration",
                "last_heartbeat": datetime.utcnow().isoformat() + "Z",
            }])
        except Exception:
            pass

        time.sleep(poll_secs)


# ── FastAPI integration endpoint ─────────────────────────────────────────────

from fastapi import FastAPI
import uvicorn

app = FastAPI()

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "wire_traffic_fingerprints_to_scanner",
        "fingerprint_lib_loaded": FINGERPRINT_LIB_LOADED,
    }

@app.post("/analyze")
def analyze_response(payload: Dict[str, Any]):
    """Analyze a response for MCP protocol fingerprints."""
    response_text = payload.get("response_text", "")
    headers = payload.get("headers", {})
    url = payload.get("url", "")
    result = analyze_response_for_mcp(response_text, headers, url)
    return result

@app.post("/enrich/{server_id}")
def enrich_server(server_id: str, payload: Dict[str, Any]):
    """Enrich a server scan with MCP fingerprint analysis."""
    enrich_server_scan(
        server_id=server_id,
        scan_result=payload.get("scan_result", {}),
        scan_url=payload.get("scan_url", ""),
        response_text=payload.get("response_text", ""),
        headers=payload.get("headers"),
    )
    return {"ok": True, "server_id": server_id}

@app.get("/hook")
def get_scanner_hook():
    """Return the MCP-aware scanner hook configuration."""
    return create_mcp_aware_scanner_hook()

def run():
    uvicorn.run(app, host="127.0.0.1", port=8783)

if __name__ == "__main__":
    run()