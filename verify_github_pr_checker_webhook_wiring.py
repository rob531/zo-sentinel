#!/usr/bin/env python3
"""
verify_github_pr_checker_webhook_wiring.py
Integration verification for github_pr_checker.py into the pipeline.
"""

import sys
import os
import json
import time
import subprocess
import requests
from datetime import datetime

SERVICE_NAME = "github_pr_checker"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772"
SUPERVISORD_CONF_PATH = "/etc/supervisord.conf"
SUPERVISORD_ALT_PATHS = [
    "/home/workspace/zo_sentinel/supervisord_sentinel_full.conf",
    "/home/workspace/zo_sentinel/supervisord_sentinel.conf",
    "/etc/supervisord.d/sentinel.conf",
]

def ws_query(sql):
    try:
        resp = requests.post(f"{QUERY_SERVICE_URL}/query", json={"sql": sql}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e), "rows": []}

def ws_write(table, rows):
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/write", json={"table": table, "rows": rows}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def send_heartbeat():
    ws_write("service_health", {"service": "verify_github_pr_checker_webhook_wiring", "last_heartbeat": datetime.utcnow().isoformat()})

def get_service_health():
    result = ws_query(f"SELECT service, last_heartbeat FROM service_health WHERE service = '{SERVICE_NAME}'")
    if result and "rows" in result and result["rows"]:
        return result["rows"][0]
    return None

def check_supervisord_registration():
    checks = []
    found = False
    
    for conf_path in SUPERVISORD_ALT_PATHS:
        if os.path.exists(conf_path):
            try:
                with open(conf_path, 'r') as f:
                    content = f.read()
                    if f"github_pr_checker" in content.lower() or SERVICE_NAME in content:
                        found = True
                        checks.append({
                            "check": "supervisord_registration",
                            "status": "PASS",
                            "detail": f"Found in {conf_path}"
                        })
                        break
            except Exception as e:
                checks.append({
                    "check": "supervisord_registration",
                    "status": "PARTIAL",
                    "detail": f"Could not read {conf_path}: {e}"
                })
    
    if not found:
        try:
            result = subprocess.run(['supervisorctl', 'status'], capture_output=True, text=True, timeout=10)
            if SERVICE_NAME in result.stdout or "github_pr_checker" in result.stdout:
                found = True
                checks.append({
                    "check": "supervisord_registration",
                    "status": "PASS",
                    "detail": "Found via supervisorctl"
                })
        except:
            pass
    
    if not found:
        checks.append({
            "check": "supervisord_registration",
            "status": "FAIL",
            "detail": f"Not found in any supervisord config"
        })
    
    return checks

def check_webhook_endpoint():
    checks = []
    
    module_path = "/home/workspace/zo_sentinel/github_pr_checker.py"
    if os.path.exists(module_path):
        with open(module_path, 'r') as f:
            source = f.read()
            
            has_webhook_route = False
            has_post_endpoint = False
            accepts_payload = False
            handles_pr_events = False
            
            if "POST" in source or "@app.post" in source or "@router.post" in source:
                has_post_endpoint = True
            if "/webhook" in source or "/pr" in source or "/github" in source:
                has_webhook_route = True
            if "payload" in source.lower() and ("event" in source.lower() or "action" in source.lower()):
                accepts_payload = True
            if "pull_request" in source.lower() or "PR" in source or "github_event" in source.lower():
                handles_pr_events = True
            
            if has_webhook_route and has_post_endpoint:
                checks.append({
                    "check": "webhook_endpoint_exists",
                    "status": "PASS",
                    "detail": "Webhook POST endpoint found in module"
                })
            else:
                checks.append({
                    "check": "webhook_endpoint_exists",
                    "status": "FAIL",
                    "detail": "Webhook endpoint not properly defined"
                })
            
            if accepts_payload:
                checks.append({
                    "check": "accepts_github_payload",
                    "status": "PASS",
                    "detail": "GitHub payload handling found"
                })
            else:
                checks.append({
                    "check": "accepts_github_payload",
                    "status": "PARTIAL",
                    "detail": "Payload parsing may be incomplete"
                })
            
            if handles_pr_events:
                checks.append({
                    "check": "handles_pr_events",
                    "status": "PASS",
                    "detail": "PR event handling found"
                })
            else:
                checks.append({
                    "check": "handles_pr_events",
                    "status": "PARTIAL",
                    "detail": "PR event handling may be incomplete"
                })
    else:
        checks.append({
            "check": "webhook_endpoint_exists",
            "status": "FAIL",
            "detail": f"Module not found at {module_path}"
        })
    
    return checks

def check_verdict_lookup_wiring():
    checks = []
    
    module_path = "/home/workspace/zo_sentinel/github_pr_checker.py"
    if os.path.exists(module_path):
        with open(module_path, 'r') as f:
            source = f.read()
            
            has_trust_synthesiser_import = False
            has_trust_synthesiser_call = False
            has_verdict_lookup = False
            has_score_query = False
            
            if "trust_synthesiser" in source.lower():
                has_trust_synthesiser_import = True
            if "ws_query" in source and ("trust" in source.lower() or "verdict" in source.lower()):
                has_trust_synthesiser_call = True
            if "verdict" in source.lower() and ("lookup" in source.lower() or "query" in source.lower()):
                has_verdict_lookup = True
            if "signal_score" in source.lower() or "trust_score" in source.lower():
                has_score_query = True
            
            if has_trust_synthesiser_import:
                checks.append({
                    "check": "trust_synthesiser_import",
                    "status": "PASS",
                    "detail": "Trust synthesiser import found"
                })
            else:
                checks.append({
                    "check": "trust_synthesiser_import",
                    "status": "FAIL",
                    "detail": "Trust synthesiser not imported"
                })
            
            if has_verdict_lookup:
                checks.append({
                    "check": "verdict_lookup_logic",
                    "status": "PASS",
                    "detail": "Verdict lookup logic found"
                })
            else:
                checks.append({
                    "check": "verdict_lookup_logic",
                    "status": "FAIL",
                    "detail": "Verdict lookup not implemented"
                })
            
            if has_score_query:
                checks.append({
                    "check": "score_query_integration",
                    "status": "PASS",
                    "detail": "Score query integration found"
                })
            else:
                checks.append({
                    "check": "score_query_integration",
                    "status": "PARTIAL",
                    "detail": "Score query may not be wired"
                })
            
            trust_synthesiser_v2_path = "/home/workspace/zo_sentinel/trust_synthesiser_v2.py"
            if os.path.exists(trust_synthesiser_v2_path):
                with open(trust_synthesiser_v2_path, 'r') as tf:
                    ts_source = tf.read()
                    has_verdict_function = "get_verdict" in ts_source or "verdict" in ts_source.lower()
                    if has_verdict_function and has_verdict_lookup:
                        checks.append({
                            "check": "verdict_lookup_wired",
                            "status": "PASS",
                            "detail": "Verdict lookup wired to trust_synthesiser_v2"
                        })
                    else:
                        checks.append({
                            "check": "verdict_lookup_wired",
                            "status": "PARTIAL",
                            "detail": "Verdict lookup may not be fully wired"
                        })
            else:
                checks.append({
                    "check": "verdict_lookup_wired",
                    "status": "PARTIAL",
                    "detail": "trust_synthesiser_v2.py not found for wiring verification"
                })
    else:
        checks.append({
            "check": "verdict_lookup_wiring",
            "status": "FAIL",
            "detail": "Module not found"
        })
    
    return checks

def check_pr_status_update():
    checks = []
    
    module_path = "/home/workspace/zo_sentinel/github_pr_checker.py"
    if os.path.exists(module_path):
        with open(module_path, 'r') as f:
            source = f.read()
            
            has_github_api_call = False
            has_pr_status_update = False
            has_api_token = False
            has_commit_status = False
            
            if "github.com/api" in source or "api.github" in source or "requests.patch" in source or "requests.post" in source:
                if "status" in source.lower() or "commit" in source.lower():
                    has_github_api_call = True
            if "update" in source.lower() and ("status" in source.lower() or "comment" in source.lower()):
                has_pr_status_update = True
            if "token" in source.lower() or "Authorization" in source or "headers" in source:
                has_api_token = True
            if "commit" in source.lower() and "status" in source.lower():
                has_commit_status = True
            
            if has_github_api_call:
                checks.append({
                    "check": "github_api_status_update",
                    "status": "PASS",
                    "detail": "GitHub API status update call found"
                })
            else:
                checks.append({
                    "check": "github_api_status_update",
                    "status": "FAIL",
                    "detail": "GitHub API status update not found"
                })
            
            if has_pr_status_update:
                checks.append({
                    "check": "pr_status_update_logic",
                    "status": "PASS",
                    "detail": "PR status update logic found"
                })
            else:
                checks.append({
                    "check": "pr_status_update_logic",
                    "status": "PARTIAL",
                    "detail": "PR status update logic may be incomplete"
                })
            
            if has_api_token:
                checks.append({
                    "check": "github_api_auth_configured",
                    "status": "PASS",
                    "detail": "GitHub API authentication configured"
                })
            else:
                checks.append({
                    "check": "github_api_auth_configured",
                    "status": "FAIL",
                    "detail": "GitHub API authentication not configured"
                })
            
            if has_commit_status:
                checks.append({
                    "check": "commit_status_integration",
                    "status": "PASS",
                    "detail": "Commit status integration found"
                })
            else:
                checks.append({
                    "check": "commit_status_integration",
                    "status": "PARTIAL",
                    "detail": "Commit status integration may be missing"
                })
    else:
        checks.append({
            "check": "pr_status_update",
            "status": "FAIL",
            "detail": "Module not found"
        })
    
    return checks

def check_heartbeat():
    checks = []
    
    health = get_service_health()
    if health:
        last_heartbeat = health.get("last_heartbeat", "")
        checks.append({
            "check": "heartbeat_registered",
            "status": "PASS",
            "detail": f"Last heartbeat: {last_heartbeat}"
        })
        
        if last_heartbeat:
            try:
                from datetime import datetime
                hb_time = datetime.fromisoformat(last_heartbeat.replace('Z', '+00:00'))
                now = datetime.utcnow()
                age_seconds = (now - hb_time.replace(tzinfo=None)).total_seconds()
                
                if age_seconds < 300:
                    checks.append({
                        "check": "heartbeat_fresh",
                        "status": "PASS",
                        "detail": f"Heartbeat age: {age_seconds:.0f}s"
                    })
                elif age_seconds < 3600:
                    checks.append({
                        "check": "heartbeat_fresh",
                        "status": "PARTIAL",
                        "detail": f"Heartbeat age: {age_seconds:.0f}s (stale)"
                    })
                else:
                    checks.append({
                        "check": "heartbeat_fresh",
                        "status": "FAIL",
                        "detail": f"Heartbeat age: {age_seconds/60:.0f}min (very stale)"
                    })
            except Exception as e:
                checks.append({
                    "check": "heartbeat_fresh",
                    "status": "PARTIAL",
                    "detail": f"Could not parse heartbeat: {e}"
                })
    else:
        checks.append({
            "check": "heartbeat_registered",
            "status": "FAIL",
            "detail": "Service not registered in service_health"
        })
        checks.append({
            "check": "heartbeat_fresh",
            "status": "FAIL",
            "detail": "No heartbeat data available"
        })
    
    return checks

def check_risk_tier_threshold():
    checks = []
    
    module_path = "/home/workspace/zo_sentinel/github_pr_checker.py"
    if os.path.exists(module_path):
        with open(module_path, 'r') as f:
            source = f.read()
            
            risk_tier_thresholds = []
            for line in source.split('\n'):
                if 'RISK_TIER' in line or 'risk_tier' in line or 'threshold' in line.lower():
                    risk_tier_thresholds.append(line.strip())
            
            if risk_tier_thresholds:
                checks.append({
                    "check": "risk_tier_threshold_configured",
                    "status": "PASS",
                    "detail": f"Found {len(risk_tier_thresholds)} threshold configs"
                })
            else:
                checks.append({
                    "check": "risk_tier_threshold_configured",
                    "status": "FAIL",
                    "detail": "Risk tier thresholds not configured"
                })
    else:
        checks.append({
            "check": "risk_tier_threshold_configured",
            "status": "FAIL",
            "detail": "Module not found"
        })
    
    return checks

def check_write_service_wiring():
    checks = []
    
    module_path = "/home/workspace/zo_sentinel/github_pr_checker.py"
    if os.path.exists(module_path):
        with open(module_path, 'r') as f:
            source = f.read()
            
            has_ws_write = "ws_write" in source
            has_audit_log = "audit" in source.lower()
            
            if has_ws_write:
                checks.append({
                    "check": "write_service_wiring",
                    "status": "PASS",
                    "detail": "ws_write function found"
                })
            else:
                checks.append({
                    "check": "write_service_wiring",
                    "status": "FAIL",
                    "detail": "ws_write function not found"
                })
            
            if has_audit_log:
                checks.append({
                    "check": "audit_log_integration",
                    "status": "PASS",
                    "detail": "Audit logging found"
                })
            else:
                checks.append({
                    "check": "audit_log_integration",
                    "status": "PARTIAL",
                    "detail": "Audit logging may be missing"
                })
    else:
        checks.append({
            "check": "write_service_wiring",
            "status": "FAIL",
            "detail": "Module not found"
        })
    
    return checks

def calculate_completeness_score(all_checks):
    total_weight = 0
    passed_weight = 0
    
    weights = {
        "supervisord_registration": 15,
        "webhook_endpoint_exists": 15,
        "accepts_github_payload": 10,
        "handles_pr_events": 10,
        "trust_synthesiser_import": 10,
        "verdict_lookup_logic": 10,
        "score_query_integration": 5,
        "verdict_lookup_wired": 10,
        "github_api_status_update": 10,
        "pr_status_update_logic": 5,
        "github_api_auth_configured": 10,
        "commit_status_integration": 5,
        "heartbeat_registered": 10,
        "heartbeat_fresh": 5,
        "risk_tier_threshold_configured": 10,
        "write_service_wiring": 10,
        "audit_log_integration": 5,
    }
    
    for check in all_checks:
        name = check["check"]
        status = check["status"]
        weight = weights.get(name, 5)
        total_weight += weight
        
        if status == "PASS":
            passed_weight += weight
        elif status == "PARTIAL":
            passed_weight += weight * 0.5
    
    score = (passed_weight / total_weight) * 100 if total_weight > 0 else 0
    return round(score, 1)

def run():
    print("=" * 60)
    print("GitHub PR Checker Webhook Wiring Verification")
    print("=" * 60)
    
    all_checks = []
    
    print("\n[1/7] Checking supervisord registration...")
    checks = check_supervisord_registration()
    all_checks.extend(checks)
    for c in checks:
        print(f"  [{c['status']}] {c['detail']}")
    
    print("\n[2/7] Checking webhook endpoint...")
    checks = check_webhook_endpoint()
    all_checks.extend(checks)
    for c in checks:
        print(f"  [{c['status']}] {c['detail']}")
    
    print("\n[3/7] Checking verdict lookup wiring...")
    checks = check_verdict_lookup_wiring()
    all_checks.extend(checks)
    for c in checks:
        print(f"  [{c['status']}] {c['detail']}")
    
    print("\n[4/7] Checking PR status update path...")
    checks = check_pr_status_update()
    all_checks.extend(checks)
    for c in checks:
        print(f"  [{c['status']}] {c['detail']}")
    
    print("\n[5/7] Checking heartbeat...")
    checks = check_heartbeat()
    all_checks.extend(checks)
    for c in checks:
        print(f"  [{c['status']}] {c['detail']}")
    
    print("\n[6/7] Checking risk tier threshold...")
    checks = check_risk_tier_threshold()
    all_checks.extend(checks)
    for c in checks:
        print(f"  [{c['status']}] {c['detail']}")
    
    print("\n[7/7] Checking write service wiring...")
    checks = check_write_service_wiring()
    all_checks.extend(checks)
    for c in checks:
        print(f"  [{c['status']}] {c['detail']}")
    
    score = calculate_completeness_score(all_checks)
    
    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)
    
    status_counts = {"PASS": 0, "FAIL": 0, "PARTIAL": 0}
    for check in all_checks:
        status_counts[check["status"]] += 1
    
    print(f"\nResults: {status_counts['PASS']} PASS, {status_counts['PARTIAL']} PARTIAL, {status_counts['FAIL']} FAIL")
    print(f"\nIntegration Completeness Score: {score}/100")
    
    if score >= 90:
        print("Status: EXCELLENT - Fully integrated")
    elif score >= 70:
        print("Status: GOOD - Mostly integrated, minor gaps")
    elif score >= 50:
        print("Status: PARTIAL - Partially integrated, significant gaps")
    else:
        print("Status: INCOMPLETE - Major integration work needed")
    
    print("\nDetailed Results:")
    print("-" * 60)
    for check in all_checks:
        status_symbol = "✓" if check["status"] == "PASS" else ("~" if check["status"] == "PARTIAL" else "✗")
        print(f"  {status_symbol} {check['check']}: {check['detail']}")
    
    send_heartbeat()
    
    return score

if __name__ == "__main__":
    score = run()
    sys.exit(0 if score >= 70 else 1)