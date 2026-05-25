import os
import sys
import time
import json
import signal
import statistics
from datetime import datetime
from typing import Dict, List, Any, Optional

PID_FILE = "/tmp/signal_enrichment_aggregator.pid"
SERVICE_NAME = "signal_enrichment_aggregator"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_URL = "http://127.0.0.1:8772/query"
HEARTBEAT_INTERVAL = 60
POLL_SECS = 300
START_TIME = time.time()

SIGNAL_TYPES = [
    "supply_chain",
    "community_signal",
    "domain_trust",
    "tool_description_safety",
    "permission_scope",
    "temporal_stability"
]

ENRICHER_NAMES = {
    "supply_chain": "supply_chain_enrichment",
    "community_signal": "community_signal_enrichment",
    "domain_trust": "domain_trust_enrichment",
    "tool_description_safety": "tool_description_safety_enrichment",
    "permission_scope": "permission_scope_enrichment",
    "temporal_stability": "temporal_stability_enrichment"
}

MIN_DISTINCT_THRESHOLD = 5


def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def signal_handler(signum, frame):
    remove_pid_file()
    sys.exit(0)


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            if os.path.exists(f"/proc/{old_pid}"):
                return False
        except Exception:
            pass
    try:
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
        return True
    except Exception:
        return False


def ws_query(sql: str) -> Dict[str, Any]:
    try:
        import requests
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"rows": [], "count": 0, "error": str(e)}


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        import requests
        resp = requests.post(WRITE_SERVICE_URL, json={
            "table": table,
            "rows": rows,
            "wait": True
        }, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"Write error: {e}")
        return False


def send_heartbeat():
    try:
        uptime = int(time.time() - START_TIME)
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "last_heartbeat": datetime.utcnow().isoformat()
        }])
    except Exception:
        pass


def get_all_enrichments() -> List[Dict[str, Any]]:
    query = """
    SELECT server_id, signal_type, score, evidence, scored_at
    FROM mcp_signal_enrichments
    """
    result = ws_query(query)
    return result.get("rows", [])


def get_all_servers() -> int:
    result = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry")
    if result.get("rows"):
        return result["rows"][0].get("cnt", 0)
    return 0


def compute_discrimination_metrics(scores: List[float]) -> Dict[str, Any]:
    if not scores:
        return {
            "count": 0,
            "distinct_count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "variance": None,
            "stddev": None,
            "range": None
        }
    
    distinct = len(set(scores))
    mean_val = statistics.mean(scores)
    median_val = statistics.median(scores)
    variance_val = statistics.variance(scores) if len(scores) > 1 else 0.0
    stddev_val = statistics.stdev(scores) if len(scores) > 1 else 0.0
    
    return {
        "count": len(scores),
        "distinct_count": distinct,
        "min": min(scores),
        "max": max(scores),
        "mean": round(mean_val, 6),
        "median": round(median_val, 6),
        "variance": round(variance_val, 6),
        "stddev": round(stddev_val, 6),
        "range": round(max(scores) - min(scores), 6)
    }


def evaluate_discrimination(metrics: Dict[str, Any]) -> Dict[str, Any]:
    distinct = metrics.get("distinct_count", 0)
    stddev = metrics.get("stddev", 0)
    range_val = metrics.get("range", 0)
    
    status = "GOOD"
    issues = []
    
    if distinct < MIN_DISTINCT_THRESHOLD:
        status = "BAD"
        issues.append(f"Only {distinct} distinct values (minimum: {MIN_DISTINCT_THRESHOLD})")
    
    if stddev < 0.01 and distinct >= MIN_DISTINCT_THRESHOLD:
        status = "WEAK"
        issues.append(f"Very low stddev: {stddev}")
    
    if range_val < 0.1 and distinct >= MIN_DISTINCT_THRESHOLD:
        issues.append(f"Narrow range: {range_val}")
    
    return {
        "status": status,
        "issues": issues
    }


def compute_signal_type_report(enrichments: List[Dict[str, Any]], signal_type: str) -> Dict[str, Any]:
    relevant = [e for e in enrichments if e.get("signal_type") == signal_type]
    
    scores = []
    for e in relevant:
        score = e.get("score")
        if score is not None:
            try:
                scores.append(float(score))
            except (ValueError, TypeError):
                pass
    
    metrics = compute_discrimination_metrics(scores)
    evaluation = evaluate_discrimination(metrics)
    
    servers_covered = len(set(e.get("server_id") for e in relevant))
    
    return {
        "enricher": ENRICHER_NAMES.get(signal_type, signal_type),
        "signal_type": signal_type,
        "servers_covered": servers_covered,
        "total_records": len(relevant),
        "metrics": metrics,
        "evaluation": evaluation
    }


def compute_global_discrimination(enrichments: List[Dict[str, Any]]) -> Dict[str, Any]:
    all_scores = []
    for e in enrichments:
        score = e.get("score")
        if score is not None:
            try:
                all_scores.append(float(score))
            except (ValueError, TypeError):
                pass
    
    if not all_scores:
        return {"total_records": 0, "global_metrics": {}}
    
    global_metrics = compute_discrimination_metrics(all_scores)
    
    by_enricher = {}
    for stype in SIGNAL_TYPES:
        relevant = [e for e in enrichments if e.get("signal_type") == stype]
        scores = []
        for e in relevant:
            score = e.get("score")
            if score is not None:
                try:
                    scores.append(float(score))
                except (ValueError, TypeError):
                    pass
        if scores:
            by_enricher[stype] = {
                "count": len(scores),
                "distinct": len(set(scores))
            }
    
    return {
        "total_records": len(all_scores),
        "global_metrics": global_metrics,
        "scores_by_enricher": by_enricher
    }


def generate_discrimination_report(enrichments: List[Dict[str, Any]], total_servers: int) -> Dict[str, Any]:
    report = {
        "generated_at": datetime.utcnow().isoformat(),
        "total_servers_in_registry": total_servers,
        "total_enrichment_records": len(enrichments),
        "signal_reports": {},
        "global": {},
        "summary": {
            "total_enrichers": len(SIGNAL_TYPES),
            "good_enrichers": 0,
            "weak_enrichers": 0,
            "bad_enrichers": 0
        }
    }
    
    for signal_type in SIGNAL_TYPES:
        signal_report = compute_signal_type_report(enrichments, signal_type)
        report["signal_reports"][signal_type] = signal_report
        
        status = signal_report["evaluation"]["status"]
        if status == "GOOD":
            report["summary"]["good_enrichers"] += 1
        elif status == "WEAK":
            report["summary"]["weak_enrichers"] += 1
        elif status == "BAD":
            report["summary"]["bad_enrichers"] += 1
    
    report["global"] = compute_global_discrimination(enrichments)
    
    report["summary"]["coverage_pct"] = round(
        len(set(e.get("server_id") for e in enrichments)) / total_servers * 100, 2
    ) if total_servers > 0 else 0
    
    return report


def log_report(report: Dict[str, Any]):
    print("=" * 80)
    print(f"SIGNAL ENRICHMENT DISCRIMINATION REPORT")
    print(f"Generated: {report['generated_at']}")
    print("=" * 80)
    print(f"Total servers in registry: {report['total_servers_in_registry']}")
    print(f"Total enrichment records: {report['total_enrichment_records']}")
    print()
    
    print("SUMMARY:")
    print(f"  Good enrichers: {report['summary']['good_enrichers']}")
    print(f"  Weak enrichers: {report['summary']['weak_enrichers']}")
    print(f"  Bad enrichers: {report['summary']['bad_enrichers']}")
    print(f"  Coverage: {report['summary']['coverage_pct']}%")
    print()
    
    print("-" * 80)
    print(f"{'Enricher':<30} {'Status':<8} {'Distinct':<10} {'StdDev':<12} {'Range':<10} {'Servers'}")
    print("-" * 80)
    
    for stype, sreport in report["signal_reports"].items():
        m = sreport["metrics"]
        ev = sreport["evaluation"]
        status_indicator = "✓" if ev["status"] == "GOOD" else ("~" if ev["status"] == "WEAK" else "✗")
        print(f"{sreport['enricher']:<30} {status_indicator} {ev['status']:<6} {m.get('distinct_count', 0):<10} "
              f"{m.get('stddev', 0):<12.6f} {m.get('range', 0):<10.4f} {sreport['servers_covered']}")
        
        if ev["issues"]:
            for issue in ev["issues"]:
                print(f"    ! {issue}")
    
    print("-" * 80)
    
    global_data = report.get("global", {})
    if global_data.get("global_metrics"):
        gm = global_data["global_metrics"]
        print()
        print("GLOBAL METRICS:")
        print(f"  Distinct values: {gm.get('distinct_count', 0)}")
        print(f"  StdDev: {gm.get('stddev', 0):.6f}")
        print(f"  Range: {gm.get('range', 0):.4f}")
        print(f"  Mean: {gm.get('mean', 0):.6f}")
    
    print("=" * 80)


def save_report_to_file(report: Dict[str, Any], filepath: str = "/home/workspace/zo_sentinel/discrimination_report.json"):
    try:
        with open(filepath, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"Report saved to: {filepath}")
    except Exception as e:
        print(f"Failed to save report: {e}")


def cycle():
    print(f"[{datetime.utcnow().isoformat()}] Starting signal enrichment aggregation cycle")
    
    total_servers = get_all_servers()
    print(f"Total servers in registry: {total_servers}")
    
    enrichments = get_all_enrichments()
    print(f"Retrieved {len(enrichments)} enrichment records")
    
    if not enrichments:
        print("No enrichment data found. Ensure enrichment modules have run first.")
        return
    
    report = generate_discrimination_report(enrichments, total_servers)
    
    log_report(report)
    
    save_report_to_file(report)
    
    print(f"[{datetime.utcnow().isoformat()}] Aggregation cycle complete")


def run():
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        print(f"{SERVICE_NAME} is already running. Exiting.")
        sys.exit(1)
    
    print(f"{SERVICE_NAME} starting...")
    
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "last_heartbeat": datetime.utcnow().isoformat()
        }])
    except Exception as e:
        print(f"Warning: Could not register with service health: {e}")
    
    while True:
        try:
            cycle()
            send_heartbeat()
        except Exception as e:
            print(f"Error in cycle: {e}")
        
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()