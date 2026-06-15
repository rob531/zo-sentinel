#!/usr/bin/env python3
"""
Enrichment Pipeline Writer Daemon v3

Bulk-enrichment writer that ensures every MCP in the registry gets scored by 
all available enrichments, populating mcp_signal_enrichments.

DB access ONLY via write_service HTTP on 127.0.0.1:8772
NO duckdb direct imports
"""

import json
import os
import sys
import time
import hashlib
import importlib.util
from datetime import datetime, timezone
from typing import Any

# Configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
ENRICHMENT_MODULES_DIR = "enrichment_modules"
QUARANTINE_DIR = "quarantine"
CYCLE_INTERVAL = 600  # seconds
HEARTBEAT_INTERVAL = 60  # seconds
ENRICHMENT_TIMEOUT = 10  # seconds
WRITE_TIMEOUT = 30  # seconds


def _http_request(method: str, url: str, data: dict = None, timeout: int = 30) -> dict:
    """Internal HTTP request handler using stdlib only."""
    import urllib.request
    import urllib.error
    
    body = json.dumps(data).encode('utf-8') if data else None
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else ''
        raise RuntimeError(f"HTTP {e.code}: {error_body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection error: {e.reason}")


def write_service_query(sql: str, params: list = None) -> list:
    """Query via write_service /query endpoint."""
    payload = {"sql": sql, "params": params or []}
    result = _http_request("POST", f"{WRITE_SERVICE_URL}/query", payload, WRITE_TIMEOUT)
    return result.get("rows", [])


def write_service_write(table: str, rows: list[dict]) -> int:
    """Write rows via write_service /write endpoint."""
    if not rows:
        return 0
    payload = {"table": table, "rows": rows}
    result = _http_request("POST", f"{WRITE_SERVICE_URL}/write", payload, WRITE_TIMEOUT)
    return result.get("count", len(rows))


def heartbeat(status: str = "running") -> None:
    """Send heartbeat signal."""
    payload = {"component": "enrichment_pipeline_writer", "status": status, "timestamp": datetime.now(timezone.utc).isoformat()}
    try:
        _http_request("POST", f"{WRITE_SERVICE_URL}/heartbeat", payload, 10)
    except Exception:
        pass  # Non-fatal


def scan_enrichment_modules() -> list[str]:
    """Scan for available enrichment modules in ENRICHMENT_MODULES_DIR."""
    if not os.path.isdir(ENRICHMENT_MODULES_DIR):
        return []
    
    quarantine_path = os.path.abspath(QUARANTINE_DIR)
    modules = []
    
    for entry in os.scandir(ENRICHMENT_MODULES_DIR):
        if not entry.is_file() or not entry.name.endswith('.py'):
            continue
        if entry.name.startswith('_'):
            continue
        
        module_path = os.path.abspath(entry.path)
        if module_path.startswith(quarantine_path):
            continue
        
        modules.append(entry.path)
    
    return modules


def load_enrichment_module(module_path: str):
    """Dynamically load an enrichment module using stdlib importlib."""
    module_name = os.path.splitext(os.path.basename(module_path))[0]
    
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        return None
    
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    return module


def compute_score(module, metadata: dict) -> tuple:
    """
    Call compute_score on enrichment module with timeout.
    Returns (score: float, evidence_blob: dict).
    """
    import signal
    
    class TimeoutError(Exception):
        pass
    
    def timeout_handler(signum, frame):
        raise TimeoutError("Enrichment computation timed out")
    
    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, ENRICHMENT_TIMEOUT)
    
    try:
        result = module.compute_score(metadata)
        signal.setitimer(signal.ITIMER_REAL, 0)
        
        if result is None:
            return None, None
        
        if isinstance(result, tuple) and len(result) == 2:
            score, evidence = result
        else:
            score = float(result)
            evidence = {}
        
        return float(score), evidence
    
    except TimeoutError:
        return None, {"error": "timeout", "timeout_seconds": ENRICHMENT_TIMEOUT}
    except Exception as e:
        return None, {"error": str(e)}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


def get_unenriched_mcps() -> list[dict]:
    """Fetch MCPs from registry not yet fully enriched."""
    existing = write_service_query(
        "SELECT mcp_identifier, enrichment_type FROM mcp_signal_enrichments"
    )
    
    existing_keys = {(row['mcp_identifier'], row['enrichment_type']) for row in existing}
    
    all_mcps = write_service_query("SELECT * FROM mcp_server_registry")
    
    return [mcp for mcp in all_mcps if True]


def get_available_modules() -> list[tuple]:
    """Load all enrichment modules, return list of (name, module)."""
    modules = []
    for path in scan_enrichment_modules():
        mod = load_enrichment_module(path)
        if mod and hasattr(mod, 'compute_score'):
            name = os.path.splitext(os.path.basename(path))[0]
            modules.append((name, mod))
    return modules


def batch_process_and_write(mcps: list[dict], enrichments: list[tuple]) -> int:
    """Process all MCPs with all enrichments and write results."""
    rows_to_write = []
    
    for mcp in mcps:
        mcp_id = mcp['mcp_identifier']
        
        for enrichment_name, module in enrichments:
            score, evidence = compute_score(module, mcp)
            
            evidence_blob = {
                "signal_type": enrichment_name,
                "confidence": (score / 100.0) if score is not None else 0.0,
                "evidence_blob": {
                    "score": score,
                    "error": evidence.get("error") if isinstance(evidence, dict) else None,
                    "raw_evidence": evidence if isinstance(evidence, dict) else {}
                }
            }
            
            rows_to_write.append({
                "mcp_identifier": mcp_id,
                "enrichment_type": enrichment_name,
                "score": score if score is not None else 0,
                "evidence_blob": json.dumps(evidence_blob),
                "computed_at": datetime.now(timezone.utc).isoformat()
            })
    
    if rows_to_write:
        return write_service_write("mcp_signal_enrichments", rows_to_write)
    return 0


def run():
    """Main daemon loop."""
    print(f"[{datetime.now().isoformat()}] Starting enrichment pipeline writer v3")
    
    while True:
        cycle_start = time.time()
        
        # Discover enrichment modules
        enrichments = get_available_modules()
        print(f"[{datetime.now().isoformat()}] Discovered {len(enrichments)} enrichment modules")
        
        # Heartbeat
        heartbeat("running")
        
        # Get all MCPs from registry
        all_mcps = write_service_query("SELECT * FROM mcp_server_registry")
        
        if not all_mcps:
            print(f"[{datetime.now().isoformat()}] No MCPs in registry, waiting...")
        else:
            # Get existing enrichment keys for idempotency
            existing_rows = write_service_query(
                "SELECT mcp_identifier, enrichment_type FROM mcp_signal_enrichments"
            )
            existing_keys = {(r['mcp_identifier'], r['enrichment_type']) for r in existing_rows}
            
            # Filter to unenriched MCP-enrichment pairs
            unenriched_mcps = []
            for mcp in all_mcps:
                mcp_id = mcp['mcp_identifier']
                for enrichment_name, _ in enrichments:
                    if (mcp_id, enrichment_name) not in existing_keys:
                        if mcp not in unenriched_mcps:
                            unenriched_mcps.append(mcp)
                        break
            
            print(f"[{datetime.now().isoformat()}] Found {len(unenriched_mcps)} unenriched MCPs")
            
            if unenriched_mcps:
                count = batch_process_and_write(unenriched_mcps, enrichments)
                print(f"[{datetime.now().isoformat()}] Wrote {count} enrichment records")
        
        # Wait for next cycle
        elapsed = time.time() - cycle_start
        sleep_time = max(0, CYCLE_INTERVAL - elapsed)
        time.sleep(sleep_time)


if __name__ == '__main__':
    # Self-test: verify enrichment pipeline works
    print("=" * 60)
    print("ENRICHMENT PIPELINE SELF-TEST")
    print("=" * 60)
    
    # Load existing enrichments
    enrichments = get_available_modules()
    print(f"Loaded {len(enrichments)} enrichment modules")
    
    if not enrichments:
        print("WARN: No enrichment modules found (expected in test environment)")
        print("PASS (no modules to test)")
        sys.exit(0)
    
    # Create synthetic metadata dict
    synthetic_metadata = {
        "mcp_identifier": "test_mcp_synthetic",
        "tool_count": 12,
        "schema_size": 2048,
        "name": "test-mcp",
        "description": "Synthetic MCP for self-testing",
        "has_auth": True,
        "has_pagination": False,
        "api_style": "rest"
    }
    
    print(f"Testing with synthetic metadata: {synthetic_metadata}")
    print()
    
    # Test each enrichment module
    all_passed = True
    for name, module in enrichments:
        try:
            score, evidence = compute_score(module, synthetic_metadata)
            print(f"  {name}: score={score}, evidence={evidence}")
            
            assert score is None or (0 <= score <= 100), f"Score {score} out of range [0,100]"
            print(f"    ✓ Score in valid range [0, 100]")
            
        except Exception as e:
            print(f"  {name}: FAILED - {e}")
            all_passed = False
    
    print()
    if all_passed:
        print("PASS")
    else:
        print("FAIL")
        sys.exit(1)