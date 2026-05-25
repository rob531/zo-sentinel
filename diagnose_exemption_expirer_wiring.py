import requests
import os
import inspect
import importlib.util
from datetime import datetime, timezone

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXEMPTION_EXPIRER_PATH = "/home/workspace/zo_sentinel/exemption_expirer.py"
EXPECTED_LOG_PATH = "/var/log/zo_sentinel/exemption_expirer.log"

def get_service_health(service_name):
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": "service_health", "query": "read", "filters": {"service": service_name}},
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            if "rows" in data and len(data["rows"]) > 0:
                return data["rows"][0]
    except Exception:
        pass
    return None

def check_supervisord_registration(service_name):
    try:
        result = os.popen("supervisorctl status").read()
        for line in result.split('\n'):
            if service_name in line:
                return {"registered": True, "status": line.strip()}
        return {"registered": False, "status": None}
    except Exception as e:
        return {"registered": False, "error": str(e)}

def verify_function_signature(module_path):
    try:
        spec = importlib.util.spec_from_file_location("exemption_expirer", module_path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            has_compute_score = hasattr(module, 'compute_score')
            has_run = hasattr(module, 'run')
            
            run_sig = None
            compute_sig = None
            
            if has_run:
                run_sig = str(inspect.signature(module.run))
            if has_compute_score:
                compute_sig = str(inspect.signature(module.compute_score))
            
            return {
                "exists": True,
                "has_compute_score": has_compute_score,
                "has_run": has_run,
                "run_signature": run_sig,
                "compute_score_signature": compute_sig
            }
    except Exception as e:
        return {"exists": False, "error": str(e)}
    return {"exists": False}

def check_log_file(log_path):
    if os.path.exists(log_path):
        stat = os.stat(log_path)
        return {
            "exists": True,
            "size_bytes": stat.st_size,
            "modified_ago_seconds": (datetime.now() - datetime.fromtimestamp(stat.st_mtime)).total_seconds()
        }
    return {"exists": False}

def run():
    timestamp = datetime.now(timezone.utc).isoformat()
    
    health_record = get_service_health("exemption_expirer")
    supervisord_status = check_supervisord_registration("exemption_expirer")
    func_sig = verify_function_signature(EXEMPTION_EXPIRER_PATH)
    log_status = check_log_file(EXPECTED_LOG_PATH)
    
    health_age_seconds = None
    if health_record and 'last_heartbeat' in health_record:
        try:
            hb_time = datetime.fromisoformat(health_record['last_heartbeat'].replace('Z', '+00:00'))
            health_age_seconds = (datetime.now(timezone.utc) - hb_time).total_seconds()
        except Exception:
            health_age_seconds = None
    
    all_ok = (
        health_record is not None and
        health_age_seconds is not None and health_age_seconds < 120 and
        supervisord_status.get('registered', False) and
        func_sig.get('exists', False) and
        func_sig.get('has_run', False)
    )
    
    diagnostic = {
        "service": "diagnose_exemption_expirer_wiring",
        "timestamp": timestamp,
        "checks": {
            "supervisord_registration": supervisord_status,
            "service_health_record": health_record,
            "health_age_seconds": health_age_seconds,
            "function_signatures": func_sig,
            "log_file": log_status
        },
        "overall_status": "OK" if all_ok else "DEGRADED",
        "healthy": all_ok
    }
    
    try:
        requests.post(
            WRITE_SERVICE_URL,
            json={
                "table": "service_health",
                "rows": {
                    "service": "diagnose_exemption_expirer_wiring",
                    "last_heartbeat": timestamp,
                    "status_detail": str(diagnostic)
                },
                "wait": True
            },
            timeout=5
        )
    except Exception:
        pass
    
    print(f"[diagnose_exemption_expirer_wiring] Status: {diagnostic['overall_status']}")
    print(f"  Health record age: {health_age_seconds}s" if health_age_seconds else "  No health record")
    print(f"  Supervisord registered: {supervisord_status.get('registered', False)}")
    print(f"  Function signatures intact: {func_sig.get('exists', False)}")
    
    return diagnostic

if __name__ == "__main__":
    run()