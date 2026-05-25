#!/usr/bin/env python3
"""
signal_discrimination_enhancer.py
Diagnostic daemon to analyze signal discrimination power for:
- permission_scope
- temporal_stability
- tool_description_safety

Computes metrics: distinct values, entropy, verdict correlation.
Logs findings to service_health. Pure diagnostic only.
"""
import time
import math
import json
import requests
from collections import Counter
from datetime import datetime
from typing import Dict, List, Any, Optional

SERVICE_NAME = "signal_discrimination_enhancer"
PORT = 8786
POLL_SECS = 3600
HEARTBEAT_INTERVAL = 300
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"

SIGNAL_TYPES = ["permission_scope", "temporal_stability", "tool_description_safety"]


def log(msg: str) -> None:
    ts = datetime.utcnow().isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_query_url() -> str:
    return QUERY_SERVICE_URL


def get_execute_url() -> str:
    return EXECUTE_SERVICE_URL


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            get_write_url(),
            json={"table": table, "rows": rows, "wait": True},
            timeout=30
        )
        return resp.status_code == 200 and resp.json().get("ok", False)
    except Exception as e:
        log(f"ws_write error: {e}")
        return False


def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    try:
        resp = requests.post(
            get_query_url(),
            json={"sql": sql},
            timeout=60
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("rows", [])
        return None
    except Exception as e:
        log(f"ws_query error: {e}")
        return None


def check_single_instance() -> bool:
    import os
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            existing = int(f.read().strip())
        try:
            os.kill(existing, 0)
            log(f"Another instance running with PID {existing}. Exiting.")
            return False
        except OSError:
            log(f"Stale PID file found for {existing}. Proceeding.")
    with open(PID_FILE, "w") as f:
        f.write(str(pid))
    return True


def send_heartbeat() -> None:
    now = datetime.utcnow().isoformat()
    rows = [{"service": SERVICE_NAME, "last_heartbeat": now}]
    ws_write("service_health", rows)


def compute_entropy(values: List[float]) -> float:
    if not values:
        return 0.0
    counts = Counter(values)
    total = len(values)
    entropy = 0.0
    for count in counts.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log2(p)
    max_entropy = math.log2(len(counts)) if counts else 1
    return entropy / max_entropy if max_entropy > 0 else 0.0


def get_distinct_count(values: List[float]) -> int:
    return len(set(values))


def get_value_distribution(values: List[float], bins: int = 5) -> Dict[str, int]:
    if not values:
        return {}
    min_v, max_v = min(values), max(values)
    if min_v == max_v:
        return {"single_value": len(values)}
    bucket_size = (max_v - min_v) / bins
    distribution = {}
    for v in values:
        bucket_idx = min(int((v - min_v) / bucket_size), bins - 1)
        bucket_label = f"{min_v + bucket_idx * bucket_size:.2f}-{min_v + (bucket_idx + 1) * bucket_size:.2f}"
        distribution[bucket_label] = distribution.get(bucket_label, 0) + 1
    return distribution


def compute_verdict_correlation(scores: List[Dict[str, Any]], verdicts: Dict[str, str]) -> float:
    if not scores:
        return 0.0
    verdict_map = {"TRUSTED": 1.0, "CAUTION": 0.5, "UNKNOWN": 0.5, "UNTRUSTED": 0.0, "MALICIOUS": 0.0}
    score_values = []
    verdict_values = []
    for s in scores:
        server_id = s.get("server_id", "")
        score = s.get("score", 0.0)
        verdict = verdicts.get(server_id, "UNKNOWN")
        v_score = verdict_map.get(verdict, 0.5)
        if score > 0 or verdict != "UNKNOWN":
            score_values.append(score)
            verdict_values.append(v_score)
    if len(score_values) < 10:
        return 0.0
    mean_x = sum(score_values) / len(score_values)
    mean_y = sum(verdict_values) / len(verdict_values)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(score_values, verdict_values))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in score_values))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in verdict_values))
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def fetch_verdicts() -> Dict[str, str]:
    sql = "SELECT server_id, verdict FROM mcp_server_registry"
    rows = ws_query(sql)
    if not rows:
        return {}
    return {r.get("server_id", ""): r.get("verdict", "UNKNOWN") for r in rows}


def analyze_signal(signal_name: str) -> Dict[str, Any]:
    sql = f"""
    SELECT server_id, score
    FROM mcp_signal_scores
    WHERE signal_name = '{signal_name}'
    AND score IS NOT NULL
    """
    scores = ws_query(sql)
    if not scores:
        return {
            "signal": signal_name,
            "status": "NO_DATA",
            "distinct_values": 0,
            "entropy": 0.0,
            "verdict_correlation": 0.0,
            "total_records": 0
        }
    values = [s.get("score", 0.0) for s in scores if s.get("score") is not None]
    distinct = get_distinct_count(values)
    entropy = compute_entropy(values)
    distribution = get_value_distribution(values)
    verdicts = fetch_verdicts()
    correlation = compute_verdict_correlation(scores, verdicts)
    is_weak = distinct < 3 or entropy < 0.3 or abs(correlation) < 0.1
    return {
        "signal": signal_name,
        "status": "WEAK" if is_weak else "OK",
        "distinct_values": distinct,
        "entropy": round(entropy, 4),
        "verdict_correlation": round(correlation, 4),
        "total_records": len(values),
        "distribution": distribution,
        "min_score": round(min(values), 4) if values else 0.0,
        "max_score": round(max(values), 4) if values else 0.0,
        "avg_score": round(sum(values) / len(values), 4) if values else 0.0,
        "flags": get_weak_flags(distinct, entropy, correlation)
    }


def get_weak_flags(distinct: int, entropy: float, correlation: float) -> List[str]:
    flags = []
    if distinct < 3:
        flags.append(f"LOW_VARIANCE: only {distinct} distinct values")
    if entropy < 0.3:
        flags.append(f"LOW_ENTROPY: {entropy:.4f} normalized entropy")
    if abs(correlation) < 0.1:
        flags.append(f"WEAK_CORRELATION: {correlation:.4f} correlation with verdict")
    return flags


def run_analysis() -> Dict[str, Any]:
    log("Starting signal discrimination analysis...")
    results = []
    weak_signals = []
    for signal in SIGNAL_TYPES:
        log(f"Analyzing signal: {signal}")
        analysis = analyze_signal(signal)
        results.append(analysis)
        if analysis.get("status") == "WEAK":
            weak_signals.append(signal)
        log(f"  Status: {analysis.get('status')}, Distinct: {analysis.get('distinct_values')}, "
            f"Entropy: {analysis.get('entropy')}, Correlation: {analysis.get('verdict_correlation')}")
    summary = {
        "timestamp": datetime.utcnow().isoformat(),
        "signals_analyzed": SIGNAL_TYPES,
        "results": results,
        "weak_signals": weak_signals,
        "total_weak": len(weak_signals),
        "diagnostic_verdict": "ACTION_REQUIRED" if weak_signals else "HEALTHY"
    }
    log(f"Analysis complete. Weak signals: {weak_signals}")
    log_diagnostic_report(summary)
    return summary


def log_diagnostic_report(summary: Dict[str, Any]) -> None:
    log("=" * 60)
    log("SIGNAL DISCRIMINATION DIAGNOSTIC REPORT")
    log("=" * 60)
    for r in summary.get("results", []):
        log(f"Signal: {r.get('signal')}")
        log(f"  Status: {r.get('status')}")
        log(f"  Distinct Values: {r.get('distinct_values')}")
        log(f"  Normalized Entropy: {r.get('entropy')}")
        log(f"  Verdict Correlation: {r.get('verdict_correlation')}")
        log(f"  Total Records: {r.get('total_records')}")
        log(f"  Score Range: {r.get('min_score')} - {r.get('max_score')}")
        log(f"  Avg Score: {r.get('avg_score')}")
        if r.get("flags"):
            for flag in r.get("flags", []):
                log(f"  !! {flag}")
    log(f"Diagnostic Verdict: {summary.get('diagnostic_verdict')}")
    log(f"Weak Signals Requiring Attention: {summary.get('weak_signals')}")
    log("=" * 60)


def write_diagnostic_summary(summary: Dict[str, Any]) -> None:
    summary_str = json.dumps(summary, indent=2)
    detail = {
        "service": SERVICE_NAME,
        "detail": summary_str[:2000],
        "created_at": datetime.utcnow().isoformat()
    }
    rows = [detail]
    ws_write("service_health", rows)


def run() -> None:
    if not check_single_instance():
        return
    log(f"Starting {SERVICE_NAME}...")
    start_time = time.time()
    cycle_count = 0
    try:
        while True:
            cycle_count += 1
            log(f"Cycle {cycle_count} starting...")
            try:
                summary = run_analysis()
                write_diagnostic_summary(summary)
                send_heartbeat()
            except Exception as e:
                log(f"Analysis error: {e}")
            elapsed = time.time() - start_time
            log(f"Cycle {cycle_count} complete. Uptime: {elapsed:.0f}s. Sleeping {POLL_SECS}s...")
            time.sleep(POLL_SECS)
    except KeyboardInterrupt:
        log("Received shutdown signal")
    finally:
        import os
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        log(f"{SERVICE_NAME} stopped")


if __name__ == "__main__":
    run()