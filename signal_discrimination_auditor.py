import time
import json
import math
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path

SERVICE_NAME = "signal_discrimination_auditor"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_URL = "http://127.0.0.1:8772/query"

POLL_SECS = 14400
HEARTBEAT_INTERVAL = 300

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(SERVICE_NAME)


def check_single_instance() -> bool:
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        old_pid = int(pid_path.read_text().strip())
        try:
            import os
            os.kill(old_pid, 0)
            logger.error(f"Another instance running with PID {old_pid}")
            return False
        except OSError:
            logger.info(f"Stale PID file found, removing")
    pid_path.write_text(str(os.getpid()))
    return True


def remove_pid_file():
    try:
        Path(PID_FILE).unlink()
    except Exception:
        pass


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    exit(0)


def ws_query(sql: str) -> List[Dict[str, Any]]:
    import requests
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        logger.error(f"Query failed: {sql[:100]}... Error: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    import requests
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={
            "table": table,
            "rows": rows,
            "wait": True
        }, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Write failed to {table}: {e}")
        return False


def send_heartbeat():
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "last_heartbeat": datetime.utcnow().isoformat()
        }])
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")


def compute_stddev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(variance)


def analyze_signal_quality(signal_data: List[Dict[str, Any]]) -> Dict[str, Any]:
    signal_stats = {}
    
    by_signal = {}
    for row in signal_data:
        sig_type = row.get('signal_name', 'unknown')
        if sig_type not in by_signal:
            by_signal[sig_type] = []
        try:
            score = float(row.get('score', 0))
            by_signal[sig_type].append(score)
        except (ValueError, TypeError):
            continue
    
    for sig_type, scores in by_signal.items():
        if not scores:
            continue
        
        distinct_scores = len(set(scores))
        min_score = min(scores)
        max_score = max(scores)
        std_dev = compute_stddev(scores)
        count = len(scores)
        
        is_weak = distinct_scores < 5 or std_dev < 5.0
        
        signal_stats[sig_type] = {
            "signal_type": sig_type,
            "total_records": count,
            "distinct_scores": distinct_scores,
            "min_score": round(min_score, 4),
            "max_score": round(max_score, 4),
            "score_range": round(max_score - min_score, 4),
            "stddev": round(std_dev, 4),
            "is_weak": is_weak,
            "weak_reasons": []
        }
        
        if distinct_scores < 5:
            signal_stats[sig_type]["weak_reasons"].append(f"Only {distinct_scores} distinct scores (threshold: 5)")
        if std_dev < 5.0:
            signal_stats[sig_type]["weak_reasons"].append(f"Stddev {std_dev:.2f} below threshold 5.0")
    
    return signal_stats


def verify_evidence_blob_format(signal_data: List[Dict[str, Any]]) -> Dict[str, Any]:
    required_keys = ['signal_type', 'confidence', 'evidence_blob']
    
    verification_results = {
        "total_checked": 0,
        "valid_format": 0,
        "invalid_format": 0,
        "missing_keys_breakdown": {key: 0 for key in required_keys},
        "sample_invalid": [],
        "signal_type_compliance": {}
    }
    
    signal_compliance = {}
    
    for row in signal_data:
        verification_results["total_checked"] += 1
        
        evidence_blob = row.get('evidence', '')
        if not evidence_blob:
            verification_results["missing_keys_breakdown"]['evidence_blob'] += 1
            verification_results["invalid_format"] += 1
            verification_results["sample_invalid"].append({
                "server_id": row.get('server_id', 'unknown'),
                "signal_name": row.get('signal_name', 'unknown'),
                "reason": "empty_evidence"
            })
            continue
        
        try:
            if isinstance(evidence_blob, str):
                parsed = json.loads(evidence_blob)
            else:
                parsed = evidence_blob
            
            missing = [k for k in required_keys if k not in parsed]
            
            sig_type = row.get('signal_name', 'unknown')
            if sig_type not in signal_compliance:
                signal_compliance[sig_type] = {"compliant": 0, "non_compliant": 0}
            
            if missing:
                verification_results["invalid_format"] += 1
                signal_compliance[sig_type]["non_compliant"] += 1
                for k in missing:
                    verification_results["missing_keys_breakdown"][k] += 1
                
                if len(verification_results["sample_invalid"]) < 5:
                    verification_results["sample_invalid"].append({
                        "server_id": row.get('server_id', 'unknown'),
                        "signal_name": sig_type,
                        "reason": f"missing_keys:{','.join(missing)}"
                    })
            else:
                verification_results["valid_format"] += 1
                signal_compliance[sig_type]["compliant"] += 1
                
        except json.JSONDecodeError as e:
            verification_results["invalid_format"] += 1
            verification_results["sample_invalid"].append({
                "server_id": row.get('server_id', 'unknown'),
                "signal_name": row.get('signal_name', 'unknown'),
                "reason": f"json_parse_error: {str(e)[:50]}"
            })
    
    verification_results["signal_type_compliance"] = signal_compliance
    verification_results["compliance_rate"] = (
        verification_results["valid_format"] / verification_results["total_checked"]
        if verification_results["total_checked"] > 0 else 0
    )
    
    return verification_results


def compute_signal_discrimination_score(signal_stats: Dict[str, Any], evidence_verification: Dict[str, Any]) -> float:
    total_signals = len(signal_stats)
    if total_signals == 0:
        return 0.0
    
    strong_signals = sum(1 for s in signal_stats.values() if not s["is_weak"])
    weak_signal_penalty = (total_signals - strong_signals) * 10
    
    evidence_compliance = evidence_verification.get("compliance_rate", 1.0)
    evidence_penalty = (1.0 - evidence_compliance) * 20
    
    discrimination_score = max(0, 100 - weak_signal_penalty - evidence_penalty)
    
    return round(discrimination_score, 2)


def generate_audit_report(signal_stats: Dict[str, Any], evidence_verification: Dict[str, Any], discrimination_score: float) -> Dict[str, Any]:
    weak_signals = [s for s in signal_stats.values() if s["is_weak"]]
    strong_signals = [s for s in signal_stats.values() if not s["is_weak"]]
    
    report = {
        "audit_metadata": {
            "timestamp": datetime.utcnow().isoformat(),
            "service": SERVICE_NAME,
            "cycle": "4h",
            "report_version": "1.0"
        },
        "discrimination_score": discrimination_score,
        "overall_assessment": "GOOD" if discrimination_score >= 70 else "FAIR" if discrimination_score >= 50 else "POOR",
        "signal_quality_summary": {
            "total_signals": len(signal_stats),
            "strong_signals": len(strong_signals),
            "weak_signals": len(weak_signals),
            "weak_signal_list": [s["signal_type"] for s in weak_signals]
        },
        "signal_statistics": signal_stats,
        "evidence_blob_verification": {
            "compliance_rate": evidence_verification.get("compliance_rate", 0),
            "total_checked": evidence_verification.get("total_checked", 0),
            "valid_format": evidence_verification.get("valid_format", 0),
            "invalid_format": evidence_verification.get("invalid_format", 0),
            "per_signal_compliance": evidence_verification.get("signal_type_compliance", {})
        },
        "weak_signals_detail": [
            {
                "signal_type": s["signal_type"],
                "reasons": s["weak_reasons"],
                "distinct_scores": s["distinct_scores"],
                "stddev": s["stddev"],
                "recommendation": "Review enrichment logic and scoring thresholds"
            }
            for s in weak_signals
        ],
        "recommendations": []
    }
    
    if weak_signals:
        report["recommendations"].append({
            "priority": "HIGH",
            "action": "Review weak signal enrichment logic",
            "affected_signals": [s["signal_type"] for s in weak_signals]
        })
    
    if evidence_verification.get("compliance_rate", 1.0) < 0.95:
        report["recommendations"].append({
            "priority": "MEDIUM",
            "action": "Fix evidence_blob format compliance",
            "details": "Some signals missing required keys (signal_type, confidence, evidence_blob)"
        })
    
    avg_stddev = sum(s["stddev"] for s in signal_stats.values()) / len(signal_stats) if signal_stats else 0
    if avg_stddev < 10:
        report["recommendations"].append({
            "priority": "LOW",
            "action": "Consider adjusting scoring granularity",
            "details": f"Average stddev across signals is {avg_stddev:.2f}, indicating compressed scoring ranges"
        })
    
    return report


def write_report_to_meta(report: Dict[str, Any]) -> bool:
    try:
        meta_entry = {
            "service": SERVICE_NAME,
            "meta": json.dumps({
                "last_audit": report["audit_metadata"]["timestamp"],
                "discrimination_score": report["discrimination_score"],
                "weak_signals_count": len(report["weak_signals_detail"]),
                "evidence_compliance": report["evidence_blob_verification"]["compliance_rate"]
            })
        }
        
        result = ws_write("service_health", [meta_entry])
        
        logger.info(f"Wrote audit metadata to service_health")
        return result
    except Exception as e:
        logger.error(f"Failed to write meta: {e}")
        return False


def cycle():
    logger.info("=== Starting Signal Discrimination Audit ===")
    
    logger.info("Querying mcp_signal_scores for all signals...")
    signal_data = ws_query("SELECT server_id, signal_name, score, evidence, scored_at FROM mcp_signal_scores")
    
    if not signal_data:
        logger.warning("No signal data found in mcp_signal_scores")
        return
    
    logger.info(f"Retrieved {len(signal_data)} signal records")
    
    logger.info("Analyzing signal quality...")
    signal_stats = analyze_signal_quality(signal_data)
    logger.info(f"Found {len(signal_stats)} distinct signal types")
    
    logger.info("Verifying evidence_blob format compliance...")
    evidence_verification = verify_evidence_blob_format(signal_data)
    logger.info(f"Evidence compliance rate: {evidence_verification.get('compliance_rate', 0):.2%}")
    
    discrimination_score = compute_signal_discrimination_score(signal_stats, evidence_verification)
    logger.info(f"Discrimination score: {discrimination_score}")
    
    report = generate_audit_report(signal_stats, evidence_verification, discrimination_score)
    
    report_json = json.dumps(report, indent=2)
    logger.info(f"Audit Report:\n{report_json[:2000]}...")
    
    write_report_to_meta(report)
    
    weak_count = len(report["weak_signals_detail"])
    if weak_count > 0:
        logger.warning(f"⚠ Found {weak_count} weak signals requiring enrichment revision:")
        for ws in report["weak_signals_detail"]:
            logger.warning(f"  - {ws['signal_type']}: {ws['reasons']}")
    
    logger.info("=== Signal Discrimination Audit Complete ===")


def run():
    import os
    import signal
    
    logger.info(f"Starting {SERVICE_NAME}")
    
    if not check_single_instance():
        logger.error("Cannot start - another instance is running")
        return
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        while True:
            cycle()
            send_heartbeat()
            logger.info(f"Sleeping {POLL_SECS}s until next cycle...")
            time.sleep(POLL_SECS)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        remove_pid_file()


if __name__ == '__main__':
    run()