import os, time, logging, requests
from datetime import datetime, timedelta
from typing import Dict, Any, List

log = logging.getLogger(__name__)

SERVICE_NAME = "false_positive_tracker"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 300

FALSE_POSITIVE_VERDICTS = ["HIGH_RISK_ISOLATED", "KNOWN_THREAT"]
FALSE_NEGATIVE_VERDICTS = ["TRUSTED_GENERAL"]

REPORT_PATH = "/home/workspace/zo_sentinel/FALSE_POSITIVE_REPORT.md"
SIGNAL_WEIGHTS_OVERRIDE_PATH = "/home/workspace/zo_sentinel/signal_weights_override.json"

def ws_query(sql: str, params: List[Any] = None) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql, "params": params or []}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])
    except Exception as e:
        log.warning(f"ws_query failed: {e}")
        return []

def ws_write(table: str, rows: Any) -> bool:
    try:
        payload = {"table": table, "rows": rows} if isinstance(rows, list) else {"table": table, "rows": [rows]}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.warning(f"ws_write failed: {e}")
        return False

def send_heartbeat() -> bool:
    return ws_write("service_health", {
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.utcnow().isoformat()
    })

def check_single_instance() -> bool:
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.info(f"Another instance running with PID {old_pid}")
            return False
        except OSError:
            pass
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    return True

def ensure_corrections_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_corrections (
        id          BIGINT PRIMARY KEY,
        server_id   VARCHAR NOT NULL,
        action      VARCHAR NOT NULL,
        reason      TEXT,
        original_verdict VARCHAR,
        analyst_decision VARCHAR,
        corrected_at TIMESTAMPTZ DEFAULT now(),
        metadata    TEXT
    )
    """
    try:
        requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=30)
    except Exception as e:
        log.warning(f"ensure_corrections_table failed: {e}")

def find_false_positives(since_hours: int = 720) -> List[Dict[str, Any]]:
    cutoff = (datetime.utcnow() - timedelta(hours=since_hours)).isoformat()
    sql = f"""
    SELECT server_id, name, verdict, analyst_decision, trust_score, reason,
           first_seen, last_assessed, last_seen
    FROM mcp_server_registry
    WHERE analyst_decision = 'APPROVED'
      AND verdict IN ('HIGH_RISK_ISOLATED', 'KNOWN_THREAT')
      AND last_assessed >= '{cutoff}'
    ORDER BY last_assessed DESC
    """
    return ws_query(sql)

def find_false_negatives(since_hours: int = 720) -> List[Dict[str, Any]]:
    cutoff = (datetime.utcnow() - timedelta(hours=since_hours)).isoformat()
    sql = f"""
    SELECT server_id, name, verdict, analyst_decision, trust_score, reason,
           first_seen, last_assessed, last_seen
    FROM mcp_server_registry
    WHERE analyst_decision = 'REJECTED'
      AND verdict = 'TRUSTED_GENERAL'
      AND last_assessed >= '{cutoff}'
    ORDER BY last_assessed DESC
    """
    return ws_query(sql)

def get_total_analyst_decisions(since_hours: int = 720) -> Dict[str, int]:
    cutoff = (datetime.utcnow() - timedelta(hours=since_hours)).isoformat()
    sql = f"""
    SELECT analyst_decision, COUNT(*) as cnt
    FROM mcp_server_registry
    WHERE analyst_decision IS NOT NULL
      AND last_assessed >= '{cutoff}'
    GROUP BY analyst_decision
    """
    results = ws_query(sql)
    return {r["analyst_decision"]: r["cnt"] for r in results}

def get_corrections_summary() -> Dict[str, Any]:
    sql = """
    SELECT action, COUNT(*) as cnt
    FROM mcp_corrections
    WHERE corrected_at >= now() - INTERVAL '30 days'
    GROUP BY action
    """
    results = ws_query(sql)
    return {r["action"]: r["cnt"] for r in results}

def record_correction(server_id: str, action: str, reason: str, original_verdict: str, analyst_decision: str) -> bool:
    correction = {
        "server_id": server_id,
        "action": action,
        "reason": reason,
        "original_verdict": original_verdict,
        "analyst_decision": analyst_decision,
        "metadata": f"recorded_at={datetime.utcnow().isoformat()}"
    }
    return ws_write("mcp_corrections", correction)

def compute_precision_recall(fp_count: int, fn_count: int, total_approved: int, total_rejected: int) -> Dict[str, float]:
    total_decisions = total_approved + total_rejected
    if total_decisions == 0:
        return {"precision": 1.0, "recall": 1.0, "fp_rate": 0.0, "fn_rate": 0.0}
    fp_rate = fp_count / total_approved if total_approved > 0 else 0.0
    fn_rate = fn_count / total_rejected if total_rejected > 0 else 0.0
    precision = 1.0 - fp_rate
    recall = 1.0 - fn_rate
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "fp_rate": round(fp_rate, 4),
        "fn_rate": round(fn_rate, 4)
    }

def generate_signal_weights_recommendations(fp_count: int, fn_count: int, precision: float, recall: float) -> Dict[str, Any]:
    recommendations = {"adjustments": [], "confidence": "low"}
    
    if precision < 0.95:
        recommendations["adjustments"].append({
            "signal": "threat_intel_boost", "adjustment": "increase", "weight_delta": -0.1,
            "reason": f"Low precision ({precision:.1%}) indicates false positives, reduce threat signal weight"
        })
    
    if recall < 0.95:
        recommendations["adjustments"].append({
            "signal": "attestation_weight", "adjustment": "decrease", "weight_delta": 0.15,
            "reason": f"Low recall ({recall:.1%}) indicates false negatives, rebalance attestation weight"
        })
    
    if fp_count > fn_count * 2:
        recommendations["adjustments"].append({
            "signal": "verdict_threshold", "adjustment": "raise", "weight_delta": 0.05,
            "reason": "High false positive rate suggests thresholds too aggressive"
        })
    
    confidence = "high" if (fp_count + fn_count) >= 50 else ("medium" if (fp_count + fn_count) >= 20 else "low")
    recommendations["confidence"] = confidence
    recommendations["total_corrections"] = fp_count + fn_count
    return recommendations

def write_false_positive_report(
    false_positives: List[Dict[str, Any]],
    false_negatives: List[Dict[str, Any]],
    precision: float,
    recall: float,
    fp_rate: float,
    fn_rate: float,
    recommendations: Dict[str, Any],
    correction_counts: Dict[str, int]
) -> str:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    
    lines = [
        "# FALSE POSITIVE REPORT",
        f"Generated: {now}",
        f"Analysis Window: Last 720 hours (30 days)",
        "",
        "## Summary Metrics",
        f"- False Positives Detected: {len(false_positives)}",
        f"- False Negatives Detected: {len(false_negatives)}",
        f"- Precision: {precision:.2%}",
        f"- Recall: {recall:.2%}",
        f"- FP Rate: {fp_rate:.2%}",
        f"- FN Rate: {fn_rate:.2%}",
        "",
        "## Historical Corrections (Last 30 Days)",
    ]
    
    for action, count in correction_counts.items():
        lines.append(f"- {action}: {count}")
    
    if false_positives:
        lines.extend(["", "## False Positives (APPROVED but HIGH_RISK_ISOLATED/KNOWN_THREAT)"])
        lines.append("| Server ID | Name | Verdict | Trust Score | Last Assessed |")
        lines.append("|-----------|------|---------|-------------|---------------|")
        for fp in false_positives[:50]:
            lines.append(f"| {fp.get('server_id','')} | {fp.get('name','')[:40]} | {fp.get('verdict','')} | {fp.get('trust_score',''):.3f} | {fp.get('last_assessed','')} |")
    
    if false_negatives:
        lines.extend(["", "## False Negatives (REJECTED but TRUSTED_GENERAL)"])
        lines.append("| Server ID | Name | Verdict | Trust Score | Last Assessed |")
        lines.append("|-----------|------|---------|-------------|---------------|")
        for fn in false_negatives[:50]:
            lines.append(f"| {fn.get('server_id','')} | {fn.get('name','')[:40]} | {fn.get('verdict','')} | {fn.get('trust_score',''):.3f} | {fn.get('last_assessed','')} |")
    
    lines.extend(["", "## Signal Weight Recommendations", ""])
    lines.append(f"Confidence Level: {recommendations.get('confidence', 'unknown')}")
    lines.append(f"Total Corrections Analyzed: {recommendations.get('total_corrections', 0)}")
    lines.append("")
    for adj in recommendations.get("adjustments", []):
        lines.append(f"- **{adj['signal']}**: {adj['adjustment']} (weight delta: {adj['weight_delta']})")
        lines.append(f"  - Reason: {adj['reason']}")
    
    lines.extend(["", "## Action Items", ""])
    if precision < 0.90:
        lines.append("1. **CRITICAL**: Precision below 90% - review threat detection thresholds")
    if recall < 0.90:
        lines.append("2. **CRITICAL**: Recall below 90% - review trust acceptance criteria")
    if len(false_positives) > 10:
        lines.append("3. High false positive volume - consider retraining signal weights")
    if len(false_negatives) > 10:
        lines.append("4. High false negative volume - rebalance risk scoring model")
    
    report_content = "\n".join(lines)
    
    with open(REPORT_PATH, "w") as f:
        f.write(report_content)
    
    return report_content

def write_signal_weights_override(recommendations: Dict[str, Any]) -> None:
    import json
    override_data = {
        "generated_at": datetime.utcnow().isoformat(),
        "source": SERVICE_NAME,
        "recommendations": recommendations
    }
    with open(SIGNAL_WEIGHTS_OVERRIDE_PATH, "w") as f:
        json.dump(override_data, f, indent=2)

def run_cycle() -> Dict[str, Any]:
    log.info(f"[{SERVICE_NAME}] Starting false positive detection cycle")
    
    ensure_corrections_table()
    
    false_positives = find_false_positives()
    false_negatives = find_false_negatives()
    
    log.info(f"Found {len(false_positives)} false positives, {len(false_negatives)} false negatives")
    
    for fp in false_positives:
        record_correction(
            fp["server_id"], "false_positive_detected",
            f"verdict={fp['verdict']} vs decision=APPROVED mismatch",
            fp["verdict"], fp.get("analyst_decision", "APPROVED")
        )
    
    for fn in false_negatives:
        record_correction(
            fn["server_id"], "false_negative_detected",
            f"verdict={fn['verdict']} vs decision=REJECTED mismatch",
            fn["verdict"], fn.get("analyst_decision", "REJECTED")
        )
    
    decision_counts = get_total_analyst_decisions()
    total_approved = decision_counts.get("APPROVED", 0)
    total_rejected = decision_counts.get("REJECTED", 0)
    
    metrics = compute_precision_recall(len(false_positives), len(false_negatives), total_approved, total_rejected)
    
    recommendations = generate_signal_weights_recommendations(
        len(false_positives), len(false_negatives), metrics["precision"], metrics["recall"]
    )
    
    correction_counts = get_corrections_summary()
    
    write_false_positive_report(
        false_positives, false_negatives, metrics["precision"], metrics["recall"],
        metrics["fp_rate"], metrics["fn_rate"], recommendations, correction_counts
    )
    
    write_signal_weights_override(recommendations)
    
    log.info(f"False positive report written to {REPORT_PATH}")
    log.info(f"Precision: {metrics['precision']:.2%}, Recall: {metrics['recall']:.2%}")
    
    return {
        "false_positives": len(false_positives),
        "false_negatives": len(false_negatives),
        "precision": metrics["precision"],
        "recall": metrics["recall"],
        "fp_rate": metrics["fp_rate"],
        "fn_rate": metrics["fn_rate"],
        "recommendations": recommendations
    }

def run() -> None:
    if not check_single_instance():
        log.info(f"[{SERVICE_NAME}] Instance check failed, exiting")
        return
    
    log.info(f"[{SERVICE_NAME}] Starting daemon")
    send_heartbeat()
    
    cycle_interval = 43200
    
    while True:
        try:
            result = run_cycle()
            log.info(f"[{SERVICE_NAME}] Cycle complete: {result}")
        except Exception as e:
            log.error(f"[{SERVICE_NAME}] Cycle failed: {e}")
        
        send_heartbeat()
        
        next_run = datetime.utcnow() + timedelta(seconds=cycle_interval)
        log.info(f"[{SERVICE_NAME}] Next cycle at {next_run.isoformat()}")
        time.sleep(cycle_interval)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run()