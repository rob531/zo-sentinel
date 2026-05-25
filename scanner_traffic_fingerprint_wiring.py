#!/usr/bin/env python3
"""
wire_mcp_scanner_traffic_fingerprints.py
Integration module: wires mcp_traffic_fingerprints into mcp_scanner for MCP protocol confirmation.
Writes results to mcp_signal_scores with signal_type='protocol_confidence'.
"""

import sys
sys.path.insert(0, '/home/workspace')

import time
import json
import requests
from datetime import datetime

# Import fingerprint detection functions
from mcp_traffic_fingerprints import detect_mcp_methods, is_mcp_traffic

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"


def write_signal_score(server_id: str, score: float, evidence: dict) -> bool:
    """Write protocol confidence signal score to mcp_signal_scores via write_service."""
    rows = [{
        "server_id": server_id,
        "signal_name": "protocol_confidence",
        "score": score,
        "evidence": json.dumps(evidence),
        "scored_at": datetime.utcnow().isoformat()
    }]
    payload = {"table": "mcp_signal_scores", "rows": rows, "wait": True}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def get_pending_scans() -> list:
    """Fetch servers pending scan from registry."""
    sql = """
    SELECT server_id, name, url 
    FROM mcp_server_registry 
    WHERE verdict IS NULL OR verdict = '' 
    LIMIT 100
    """
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("rows", [])
    except Exception:
        pass
    return []


def analyze_server_traffic(server_id: str, url: str) -> dict:
    """Analyze server for MCP protocol fingerprint signals."""
    result = {
        "server_id": server_id,
        "analyzed_at": datetime.utcnow().isoformat(),
        "has_mcp_methods": False,
        "is_mcp_protocol": False,
        "confidence_score": 0.0,
        "detected_methods": []
    }
    
    try:
        # Import scanner for making HTTP requests
        from mcp_scanner import perform_http_scan
        
        # Perform scan to get traffic data
        scan_result = perform_http_scan(url)
        
        if scan_result:
            traffic_data = scan_result.get("response_data", "")
            
            # Run MCP detection methods from fingerprints module
            detected_methods = detect_mcp_methods(traffic_data)
            is_mcp = is_mcp_traffic(traffic_data)
            
            result["detected_methods"] = detected_methods
            result["has_mcp_methods"] = len(detected_methods) > 0
            result["is_mcp_protocol"] = is_mcp
            
            # Calculate confidence score
            if is_mcp:
                base_score = 0.7
                method_bonus = min(len(detected_methods) * 0.05, 0.25)
                result["confidence_score"] = min(base_score + method_bonus, 0.95)
            else:
                result["confidence_score"] = 0.3 if detected_methods else 0.1
                
    except ImportError:
        result["error"] = "mcp_scanner module not available"
    except Exception as e:
        result["error"] = str(e)
    
    return result


def process_fingerprint_signals(server_id: str, url: str) -> bool:
    """Main entry point: analyze server and write signal scores."""
    analysis = analyze_server_traffic(server_id, url)
    
    evidence = {
        "analyzed_at": analysis["analyzed_at"],
        "has_mcp_methods": analysis["has_mcp_methods"],
        "is_mcp_protocol": analysis["is_mcp_protocol"],
        "detected_methods": analysis["detected_methods"]
    }
    
    # Write to signal scores table
    success = write_signal_score(
        server_id=server_id,
        score=analysis["confidence_score"],
        evidence=evidence
    )
    
    return success


def run_daemon(poll_secs: int = 60):
    """Daemon loop for continuous fingerprint signal processing."""
    print(f"[wire_fingerprint] Daemon started, polling every {poll_secs}s")
    
    while True:
        try:
            pending = get_pending_scans()
            processed = 0
            
            for server in pending:
                server_id = server.get("server_id")
                url = server.get("url")
                if server_id and url:
                    if process_fingerprint_signals(server_id, url):
                        processed += 1
            
            print(f"[wire_fingerprint] Processed {processed}/{len(pending)} servers")
            
        except Exception as e:
            print(f"[wire_fingerprint] Error: {e}")
        
        time.sleep(poll_secs)


def main():
    """Direct execution for single-shot analysis."""
    if len(sys.argv) > 1:
        server_id = sys.argv[1]
        url = sys.argv[2] if len(sys.argv) > 2 else None
        
        if not url:
            # Fetch from registry
            sql = f"SELECT url FROM mcp_server_registry WHERE server_id = '{server_id}'"
            try:
                resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=10)
                if resp.status_code == 200:
                    rows = resp.json().get("rows", [])
                    if rows:
                        url = rows[0].get("url")
            except Exception:
                pass
        
        if url:
            result = analyze_server_traffic(server_id, url)
            print(json.dumps(result, indent=2))
            process_fingerprint_signals(server_id, url)
        else:
            print("No URL found for server_id")
    else:
        run_daemon()


if __name__ == '__main__':
    main()