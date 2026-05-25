import os
import re
import json
import time
import glob
import hashlib
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_SERVICE = "http://127.0.0.1:8772"
EXECUTE_SERVICE = "http://127.0.0.1:8772"

SERVICE_NAME = "diagnose_build_failure_correlation"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = "/tmp/diagnose_build_failure_correlation.log"

PROJECT_ROOT = "/home/workspace/zo_sentinel"
BUILD_LOG_DIR = "/tmp"
SMOKE_LOG_DIR = "/tmp"


def log(msg: str) -> None:
    ts = datetime.utcnow().isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            log(f"Instance already running with PID {old_pid}")
            return False
        except (ValueError, ProcessLookupError, PermissionError):
            log(f"Stale PID file, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame) -> None:
    log(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    exit(0)


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        import requests
        resp = requests.post(f"{QUERY_SERVICE}/query", json={"sql": sql}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log(f"Query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        import requests
        resp = requests.post(f"{WRITE_SERVICE}/write", json={"table": table, "rows": rows, "wait": True}, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        log(f"Write failed: {e}")
        return False


def get_service_health() -> Dict[str, Dict[str, Any]]:
    query = "SELECT service, last_heartbeat FROM service_health"
    rows = ws_query(query)
    result = {}
    now = datetime.utcnow()
    for row in rows:
        svc = row.get("service", "")
        hb = row.get("last_heartbeat", "")
        if hb:
            try:
                hb_dt = datetime.fromisoformat(hb.replace("Z", "+00:00"))
                age_secs = (now - hb_dt).total_seconds()
                result[svc] = {"last_heartbeat": hb, "age_seconds": age_secs, "age_str": format_duration(age_secs)}
            except Exception:
                result[svc] = {"last_heartbeat": hb, "age_seconds": float('inf'), "age_str": "unknown"}
        else:
            result[svc] = {"last_heartbeat": None, "age_seconds": float('inf'), "age_str": "never"}
    return result


def format_duration(secs: float) -> str:
    if secs < 60:
        return f"{int(secs)}s"
    elif secs < 3600:
        return f"{int(secs // 60)}m {int(secs % 60)}s"
    elif secs < 86400:
        h = int(secs // 3600)
        m = int((secs % 3600) // 60)
        return f"{h}h {m}m"
    else:
        d = int(secs // 86400)
        h = int((secs % 86400) // 3600)
        return f"{d}d {h}h"


def find_build_logs() -> List[str]:
    patterns = [
        "/tmp/build_*.log",
        "/tmp/build*.log",
        "/home/workspace/zo_sentinel/build_*.log",
        "/home/workspace/zo_sentinel/.build_logs/*.log",
    ]
    logs = []
    for pattern in patterns:
        logs.extend(glob.glob(pattern))
    return sorted(set(logs))


def find_smoke_logs() -> List[str]:
    patterns = [
        "/tmp/smoke_*.log",
        "/tmp/smoke*.log",
        "/home/workspace/zo_sentinel/tests/smoke_*.log",
        "/home/workspace/zo_sentinel/smoke_*.log",
    ]
    logs = []
    for pattern in patterns:
        logs.extend(glob.glob(pattern))
    return sorted(set(logs))


def parse_build_log(log_path: str) -> Dict[str, Any]:
    result = {
        "path": log_path,
        "timestamp": None,
        "failed_modules": [],
        "error_types": [],
        "import_errors": [],
        "dependency_errors": [],
        "size_bytes": os.path.getsize(log_path) if os.path.exists(log_path) else 0,
        "checksum": None,
        "failed_lines": [],
    }
    
    try:
        with open(log_path, "r", errors="ignore") as f:
            content = f.read()
            if content:
                result["checksum"] = hashlib.md5(content[:10000].encode()).hexdigest()
            
            lines = content.split("\n")
            
            for line in lines:
                ts_match = re.match(r'\[?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})', line)
                if ts_match and not result["timestamp"]:
                    result["timestamp"] = ts_match.group(1)
                
                if any(x in line.lower() for x in ["error", "failed", "failure", "exception"]):
                    result["failed_lines"].append(line.strip())
                
                import_err = re.search(r'ImportError:|ModuleNotFoundError:|Import.*not found', line)
                if import_err:
                    result["import_errors"].append(line.strip())
                    result["error_types"].append("import")
                
                dep_err = re.search(r'Missing dependenc|requirement|conda|package not found', line, re.I)
                if dep_err:
                    result["dependency_errors"].append(line.strip())
                    result["error_types"].append("dependency")
            
            module_pattern = re.compile(r'(?:build|create|generate)_\w+|rewrite_\w+|retry_\w+|build_\w+')
            for line in lines:
                matches = module_pattern.findall(line)
                for m in matches:
                    if m not in result["failed_modules"]:
                        result["failed_modules"].append(m)
        
        result["error_types"] = list(set(result["error_types"]))
    except Exception as e:
        log(f"Error parsing build log {log_path}: {e}")
    
    return result


def parse_smoke_log(log_path: str) -> Dict[str, Any]:
    result = {
        "path": log_path,
        "timestamp": None,
        "failed_tests": [],
        "error_types": [],
        "import_failures": [],
        "size_bytes": os.path.getsize(log_path) if os.path.exists(log_path) else 0,
        "checksum": None,
    }
    
    try:
        with open(log_path, "r", errors="ignore") as f:
            content = f.read()
            if content:
                result["checksum"] = hashlib.md5(content[:10000].encode()).hexdigest()
            
            lines = content.split("\n")
            
            for line in lines:
                ts_match = re.match(r'\[?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})', line)
                if ts_match and not result["timestamp"]:
                    result["timestamp"] = ts_match.group(1)
                
                if any(x in line.lower() for x in ["failed", "error", "traceback"]):
                    result["failed_tests"].append(line.strip())
                
                import_fail = re.search(r'ImportError:|Import.*failed|ModuleNotFoundError', line)
                if import_fail:
                    result["import_failures"].append(line.strip())
                    result["error_types"].append("import")
            
            result["error_types"] = list(set(result["error_types"]))
    except Exception as e:
        log(f"Error parsing smoke log {log_path}: {e}")
    
    return result


def find_common_import_paths(logs: List[Dict[str, Any]]) -> Dict[str, int]:
    import_paths = defaultdict(int)
    
    import_pattern = re.compile(r'from\s+([\w\.]+)\s+import|import\s+([\w\.]+)')
    
    for log_entry in logs:
        if "import_errors" in log_entry:
            for err in log_entry.get("import_errors", []):
                matches = import_pattern.findall(err)
                for match in matches:
                    path = match[0] or match[1]
                    if path and len(path) > 3:
                        import_paths[path] += 1
    
    return dict(import_paths)


def find_common_file_patterns(logs: List[Dict[str, Any]]) -> Dict[str, int]:
    file_patterns = defaultdict(int)
    
    file_pattern = re.compile(r'/home/workspace/zo_sentinel/(\S+\.py|\S+\.html|\S+\.sh|\S+\.conf)')
    
    for log_entry in logs:
        path = log_entry.get("path", "")
        if path:
            file_patterns[path] += 1
    
    return dict(file_patterns)


def correlate_staleness_with_failures(health: Dict[str, Dict[str, Any]], logs: List[Dict]) -> Dict[str, Any]:
    correlations = []
    
    stale_threshold = 3600
    very_stale_threshold = 7200
    
    for svc, data in health.items():
        age = data.get("age_seconds", 0)
        if age > stale_threshold:
            severity = "HIGH" if age > very_stale_threshold else "MEDIUM"
            correlations.append({
                "service": svc,
                "age_seconds": age,
                "age_str": data.get("age_str", "unknown"),
                "severity": severity,
                "potential_impact": _get_stale_impact(svc)
            })
    
    correlations.sort(key=lambda x: x["age_seconds"], reverse=True)
    return {"correlations": correlations, "stale_count": len(correlations)}


def _get_stale_impact(service: str) -> str:
    impacts = {
        "write_service": "Database writes/reads fail; builds cannot persist results",
        "query_service": "Database queries fail; builds cannot validate state",
        "rug_pull_monitor": "Package integrity not monitored; stale fingerprints",
        "signal_analyser": "Signal scores not computed; trust scores incomplete",
        "trust_synthesiser": "Trust synthesis halted; verdicts stuck at UNKNOWN",
        "mcp_scanner": "New MCP servers not discovered",
        "attestation_engine": "Attestations not generated",
        "self_diagnostics": "Self-checks stale; failure detection delayed",
        "wisdom_synthesiser": "Threat intelligence not updated",
        "risk_ranker": "Risk rankings not updated",
        "threat_intel_ingestor": "Threat feeds not ingested",
    }
    return impacts.get(service, "Unknown service impact")


def analyze_failure_patterns(build_logs: List[Dict], smoke_logs: List[Dict]) -> Dict[str, Any]:
    patterns = {
        "total_build_failures": len(build_logs),
        "total_smoke_failures": len(smoke_logs),
        "common_import_paths": find_common_import_paths(build_logs + smoke_logs),
        "error_type_distribution": defaultdict(int),
        "timestamp_correlation": None,
        "file_size_distribution": [],
    }
    
    for log_entry in build_logs + smoke_logs:
        for err_type in log_entry.get("error_types", []):
            patterns["error_type_distribution"][err_type] += 1
        
        size = log_entry.get("size_bytes", 0)
        if size > 0:
            patterns["file_size_distribution"].append(size)
    
    patterns["error_type_distribution"] = dict(patterns["error_type_distribution"])
    
    if patterns["file_size_distribution"]:
        avg_size = sum(patterns["file_size_distribution"]) / len(patterns["file_size_distribution"])
        patterns["avg_log_size_bytes"] = avg_size
    
    timestamps = []
    for log_entry in build_logs + smoke_logs:
        ts = log_entry.get("timestamp")
        if ts:
            timestamps.append(ts)
    
    if len(timestamps) > 1:
        patterns["timestamp_correlation"] = "Multiple timestamps found - analyzing clustering"
    
    return patterns


def rank_root_causes(patterns: Dict, staleness: Dict, correlations: Dict) -> List[Dict[str, Any]]:
    causes = []
    
    if staleness.get("stale_count", 0) > 0:
        for corr in correlations.get("correlations", []):
            if corr["severity"] == "HIGH":
                causes.append({
                    "rank": len(causes) + 1,
                    "cause": f"STALE DAEMON: {corr['service']} ({corr['age_str']})",
                    "severity": "CRITICAL",
                    "confidence": 0.95,
                    "evidence": corr.get("potential_impact", ""),
                    "recommendation": "Restart daemon or investigate deadlock"
                })
    
    common_imports = patterns.get("common_import_paths", {})
    if common_imports:
        top_imports = sorted(common_imports.items(), key=lambda x: x[1], reverse=True)[:5]
        for path, count in top_imports:
            if count >= 2:
                causes.append({
                    "rank": len(causes) + 1,
                    "cause": f"SHARED IMPORT: {path} (found in {count} failures)",
                    "severity": "HIGH" if count >= 3 else "MEDIUM",
                    "confidence": 0.7 if count >= 3 else 0.5,
                    "evidence": f"Appears in {count} different failure logs",
                    "recommendation": "Verify module availability and version consistency"
                })
    
    error_dist = patterns.get("error_type_distribution", {})
    if error_dist:
        for err_type, count in sorted(error_dist.items(), key=lambda x: x[1], reverse=True):
            causes.append({
                "rank": len(causes) + 1,
                "cause": f"ERROR TYPE: {err_type} ({count} occurrences)",
                "severity": "MEDIUM",
                "confidence": 0.6,
                "evidence": f"Found {count} instances of {err_type} errors",
                "recommendation": f"Investigate {err_type} root cause"
            })
    
    if patterns.get("timestamp_correlation"):
        causes.append({
            "rank": len(causes) + 1,
            "cause": "SIMULTANEOUS FAILURES: Correlated timestamp suggests common trigger",
            "severity": "HIGH",
            "confidence": 0.75,
            "evidence": "Multiple builds and smoke tests failed at same time",
            "recommendation": "Check for upstream service restart or configuration change"
        })
    
    causes.sort(key=lambda x: (x["confidence"] * -1, x["severity"]))
    for i, cause in enumerate(causes):
        cause["rank"] = i + 1
    
    return causes


def get_recent_audit_events(limit: int = 100) -> List[Dict[str, Any]]:
    query = f"SELECT event_type, actor, detail, created_at FROM audit_log ORDER BY created_at DESC LIMIT {limit}"
    return ws_query(query)


def diagnose_build_failure_correlation() -> Dict[str, Any]:
    log("Starting build failure correlation diagnostic")
    
    build_logs = find_build_logs()
    smoke_logs = find_smoke_logs()
    
    log(f"Found {len(build_logs)} build logs")
    log(f"Found {len(smoke_logs)} smoke logs")
    
    parsed_build = [parse_build_log(p) for p in build_logs]
    parsed_smoke = [parse_smoke_log(p) for p in smoke_logs]
    
    filtered_build = [b for b in parsed_build if b.get("failed_lines") or b.get("import_errors")]
    filtered_smoke = [s for s in parsed_smoke if s.get("failed_tests") or s.get("import_failures")]
    
    log(f"Active build failures: {len(filtered_build)}")
    log(f"Active smoke failures: {len(filtered_smoke)}")
    
    health = get_service_health()
    log(f"Service health entries: {len(health)}")
    
    patterns = analyze_failure_patterns(filtered_build, filtered_smoke)
    
    correlations = correlate_staleness_with_failures(health, filtered_build + filtered_smoke)
    
    root_causes = rank_root_causes(patterns, correlations, correlations)
    
    result = {
        "timestamp": datetime.utcnow().isoformat(),
        "summary": {
            "total_build_failures": patterns["total_build_failures"],
            "total_smoke_failures": patterns["total_smoke_failures"],
            "active_build_failures": len(filtered_build),
            "active_smoke_failures": len(filtered_smoke),
            "stale_daemons": correlations["stale_count"],
        },
        "stale_daemon_details": correlations["correlations"],
        "failure_patterns": {
            "error_type_distribution": patterns["error_type_distribution"],
            "common_import_paths": dict(sorted(patterns["common_import_paths"].items(), key=lambda x: x[1], reverse=True)[:10]),
        },
        "ranked_root_causes": root_causes,
        "service_health_snapshot": {svc: {"age_str": data["age_str"], "age_seconds": data["age_seconds"]} for svc, data in health.items() if data.get("age_seconds", 0) > 60},
    }
    
    return result


def print_report(result: Dict[str, Any]) -> None:
    print("\n" + "=" * 80)
    print("BUILD FAILURE CORRELATION DIAGNOSTIC REPORT")
    print("=" * 80)
    
    summary = result.get("summary", {})
    print(f"\nSUMMARY")
    print(f"  Total build log files:   {summary.get('total_build_failures', 0)}")
    print(f"  Total smoke log files:  {summary.get('total_smoke_failures', 0)}")
    print(f"  Active build failures:   {summary.get('active_build_failures', 0)}")
    print(f"  Active smoke failures:   {summary.get('active_smoke_failures', 0)}")
    print(f"  Stale daemons detected:  {summary.get('stale_daemons', 0)}")
    
    health = result.get("service_health_snapshot", {})
    if health:
        print(f"\nSTALE DAEMONS (>60s old)")
        print("-" * 60)
        for svc, data in sorted(health.items(), key=lambda x: x[1].get("age_seconds", 0), reverse=True):
            age_str = data.get("age_str", "unknown")
            age_sec = data.get("age_seconds", 0)
            flag = " [CRITICAL]" if age_sec > 7200 else " [STALE]" if age_sec > 3600 else ""
            print(f"  {svc:<35} {age_str:>12}{flag}")
    
    patterns = result.get("failure_patterns", {})
    common_imports = patterns.get("common_import_paths", {})
    if common_imports:
        print(f"\nCOMMON IMPORT PATHS (shared across failures)")
        print("-" * 60)
        for path, count in list(common_imports.items())[:10]:
            print(f"  {path:<45} count={count}")
    
    error_dist = patterns.get("error_type_distribution", {})
    if error_dist:
        print(f"\nERROR TYPE DISTRIBUTION")
        print("-" * 60)
        for err_type, count in sorted(error_dist.items(), key=lambda x: x[1], reverse=True):
            print(f"  {err_type:<25} {count} occurrences")
    
    causes = result.get("ranked_root_causes", [])
    if causes:
        print(f"\nRANKED LIKELY ROOT CAUSES")
        print("-" * 80)
        for cause in causes[:15]:
            rank = cause.get("rank", "?")
            conf = cause.get("confidence", 0)
            sev = cause.get("severity", "?")
            cause_text = cause.get("cause", "")
            print(f"  #{rank:>2} [{sev:<8}] ({conf:.0%}) {cause_text}")
            evidence = cause.get("evidence", "")
            if evidence:
                print(f"       Evidence: {evidence[:70]}...")
    
    print("\n" + "=" * 80)
    print("END OF DIAGNOSTIC REPORT")
    print("=" * 80 + "\n")


def main() -> None:
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        log("Another instance is running, exiting")
        return
    
    try:
        result = diagnose_build_failure_correlation()
        print_report(result)
        
        output_path = "/tmp/diagnose_build_failure_correlation_result.json"
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        log(f"Result written to {output_path}")
        
    finally:
        remove_pid_file()


if __name__ == "__main__":
    main()