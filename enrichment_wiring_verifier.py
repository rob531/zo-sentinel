# deps: requests
import sys
import time
import json
import traceback
from typing import Any, Dict, List, Tuple

import requests

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE}/query"
WRITE_URL = f"{WRITE_SERVICE}/write"
EXECUTE_URL = f"{WRITE_SERVICE}/execute"

ENRICHMENT_MODULES = {
    "permission_scope_enrichment_v2": {
        "module_path": "/home/workspace/zo_sentinel/permission_scope_enrichment_v2.py",
        "signal_type": "permission_scope"
    },
    "temporal_stability_enrichment_v2": {
        "module_path": "/home/workspace/zo_sentinel/temporal_stability_enrichment_v2.py",
        "signal_type": "temporal_stability"
    },
    "tool_description_safety_enrichment_v2": {
        "module_path": "/home/workspace/zo_sentinel/tool_description_safety_enrichment_v2.py",
        "signal_type": "tool_description_safety"
    }
}

SYNTHETIC_METADATA = {
    "permission_scope_enrichment_v2": {
        "tools": [
            {"name": "read_file", "description": "Read a file from disk"},
            {"name": "write_file", "description": "Write content to a file"},
            {"name": "delete_file", "description": "Delete a file from the system"}
        ],
        "permissions": ["file:read", "file:write", "system:execute"]
    },
    "temporal_stability_enrichment_v2": {
        "name": "test-mcp-server",
        "version": "1.0.0",
        "created_at": "2024-01-15",
        "last_activity_at": "2024-12-01",
        "download_count": 50000,
        "release_count": 12
    },
    "tool_description_safety_enrichment_v2": {
        "name": "test-server",
        "description": "A legitimate MCP server for testing purposes",
        "tools": [
            {"name": "process_data", "description": "Process input data according to configuration"},
            {"name": "store_result", "description": "Store the computed result in the database"},
            {"name": "fetch_config", "description": "Retrieve configuration from the server"}
        ],
        "registry_source": "npm"
    }
}

def ws_query(sql: str) -> Dict[str, Any]:
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e), "rows": [], "count": 0}

def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        resp = requests.post(WRITE_URL, json={"table": table, "rows": rows}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def check_module_importable(module_path: str) -> Tuple[bool, Any, str]:
    module_name = module_path.split("/")[-1].replace(".py", "")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            return False, None, "Failed to load module spec"
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        if hasattr(module, "compute_score"):
            return True, module, "Module and compute_score() found"
        else:
            return False, None, "Module loaded but compute_score() not found"
    except Exception as e:
        return False, None, f"Import failed: {str(e)}"

def test_compute_score(module: Any, module_key: str, synthetic_metadata: Dict[str, Any]) -> Tuple[bool, float, str]:
    try:
        result = module.compute_score(synthetic_metadata)
        if isinstance(result, tuple) and len(result) == 2:
            score, evidence = result
            if not isinstance(score, (int, float)):
                return False, 0.0, f"Score is not numeric: {type(score)}"
            if not isinstance(evidence, dict):
                return False, 0.0, f"Evidence is not dict: {type(evidence)}"
            if score < 0 or score > 100:
                return False, score, f"Score {score} outside [0,100] range"
            return True, float(score), f"Valid result: score={score}, keys={list(evidence.keys())}"
        else:
            return False, 0.0, f"compute_score() returned unexpected type: {type(result)}"
    except Exception as e:
        return False, 0.0, f"compute_score() failed: {str(e)}"

def check_mcp_signal_enrichments(signal_type: str) -> Dict[str, Any]:
    sql = f"SELECT signal_type, score, evidence FROM mcp_signal_enrichments WHERE signal_type = '{signal_type}'"
    result = ws_query(sql)
    if "error" in result:
        return {"found": False, "error": result["error"], "count": 0}
    
    rows = result.get("rows", [])
    count = result.get("count", len(rows))
    
    if count == 0:
        return {"found": False, "count": 0, "scores": []}
    
    scores = [r.get("score") for r in rows if r.get("score") is not None]
    distinct_scores = len(set(scores)) if scores else 0
    
    return {
        "found": True,
        "count": count,
        "scores": scores[:10],
        "distinct_count": distinct_scores,
        "min": min(scores) if scores else None,
        "max": max(scores) if scores else None,
        "avg": sum(scores) / len(scores) if scores else None
    }

def send_heartbeat():
    try:
        requests.post(WRITE_URL, json={
            "table": "service_health",
            "rows": [{"service": "enrichment_wiring_verifier", "last_heartbeat": "now"}]
        }, timeout=5)
    except:
        pass

def main():
    print("=" * 70)
    print("ENRICHMENT WIRING VERIFIER")
    print("Checking signal_analyser_v2.py -> v2 enrichment modules -> mcp_signal_enrichments")
    print("=" * 70)
    
    all_passed = True
    results = []
    
    print("\n[1/3] Testing module imports and compute_score() signatures...")
    print("-" * 70)
    
    for module_key, config in ENRICHMENT_MODULES.items():
        module_path = config["module_path"]
        signal_type = config["signal_type"]
        
        print(f"\n  Checking: {module_key}")
        print(f"  Path: {module_path}")
        
        success, module, message = check_module_importable(module_path)
        print(f"  Import: {message}")
        
        if not success:
            print(f"  RESULT: FAILED - Cannot import module")
            all_passed = False
            results.append({"module": module_key, "import": "FAILED", "compute_score": "SKIP", "enrichments": "SKIP"})
            continue
        
        synthetic_md = SYNTHETIC_METADATA.get(module_key, {})
        score_ok, score, score_msg = test_compute_score(module, module_key, synthetic_md)
        print(f"  compute_score(): {score_msg}")
        
        if not score_ok:
            print(f"  RESULT: FAILED - compute_score() validation failed")
            all_passed = False
            results.append({"module": module_key, "import": "OK", "compute_score": "FAILED", "enrichments": "SKIP"})
            continue
        
        results.append({"module": module_key, "import": "OK", "compute_score": "OK", "score": score, "signal_type": signal_type})
        print(f"  RESULT: PASSED")
        
        send_heartbeat()
    
    print("\n" + "-" * 70)
    print("[2/3] Querying mcp_signal_enrichments for v2 signal rows...")
    print("-" * 70)
    
    enrichment_results = {}
    for result in results:
        if result.get("import") == "OK" and result.get("compute_score") == "OK":
            module_key = result["module"]
            signal_type = result["signal_type"]
            
            print(f"\n  Querying signal_type = '{signal_type}'")
            enrich_check = check_mcp_signal_enrichments(signal_type)
            
            if enrich_check.get("found"):
                print(f"  Found {enrich_check['count']} rows")
                print(f"  Distinct scores: {enrich_check['distinct_count']}")
                print(f"  Score range: [{enrich_check['min']}, {enrich_check['max']}]")
                print(f"  Avg score: {enrich_check['avg']:.2f}" if enrich_check['avg'] else "  Avg score: N/A")
                
                enrichment_results[module_key] = {
                    "found": True,
                    "count": enrich_check['count'],
                    "distinct_scores": enrich_check['distinct_count']
                }
                print(f"  RESULT: PASSED")
            else:
                print(f"  No rows found in mcp_signal_enrichments")
                enrichment_results[module_key] = {"found": False, "count": 0, "distinct_scores": 0}
                all_passed = False
                print(f"  RESULT: FAILED - No data in mcp_signal_enrichments")
            
            send_heartbeat()
    
    print("\n" + "-" * 70)
    print("[3/3] Discrimination analysis - distinct score distribution...")
    print("-" * 70)
    
    total_distinct = 0
    for module_key, enrich_data in enrichment_results.items():
        signal_type = ENRICHMENT_MODULES[module_key]["signal_type"]
        distinct = enrich_data.get("distinct_scores", 0)
        total_distinct += distinct
        print(f"  {signal_type}: {distinct} distinct score values")
    
    print(f"\n  Total distinct scores across all v2 signals: {total_distinct}")
    
    if total_distinct >= 3:
        print("  Discrimination assessment: PASSED (sufficient score variation)")
    else:
        print("  Discrimination assessment: WEAK (insufficient score variation)")
        all_passed = False
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    for result in results:
        import_status = result.get("import", "N/A")
        cs_status = result.get("compute_score", "N/A")
        enrich_status = enrichment_results.get(result["module"], {}).get("found", "N/A")
        
        overall = "PASS" if import_status == "OK" and cs_status == "OK" and enrich_status else "FAIL"
        
        print(f"  {result['module']}: import={import_status}, compute_score={cs_status}, enrichments={enrich_status} -> {overall}")
    
    print()
    if all_passed:
        print("OVERALL: PASS - All enrichment wiring checks successful")
        print("=" * 70)
        return 0
    else:
        print("OVERALL: FAIL - One or more checks failed")
        print("=" * 70)
        return 1

if __name__ == "__main__":
    sys.exit(main())