#!/usr/bin/env python3
"""
analyst_feedback_loop.py -- ZO-SENTINEL Analyst Feedback Loop Daemon

Reads MCP decisions where analysts overrode system verdicts.
Analyzes which signals were most predictive of correct outcomes.
Generates weight adjustment suggestions (human review required).
Writes WEIGHT_SUGGESTIONS.md and logs to mesh_events.

Cycle: 86400s (24 hours)
"""
import os
import sys
import time
import json
import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SERVICE_NAME = "analyst_feedback_loop"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8772/query"
HEARTBEAT_INTERVAL = 60
CYCLE_INTERVAL = 86400
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WEIGHT_SUGGESTIONS_FILE = "WEIGHT_SUGGESTIONS.md"
ZO_SENTINEL_PATH = "/home/workspace/zo_sentinel"

SIGNAL_NAMES = [
    'domain_trust',
    'tool_description_safety',
    'permission_scope',
    'supply_chain',
    'community_signal',
    'temporal_stability'
]

DEFAULT_WEIGHTS = {
    'domain_trust': 0.20,
    'tool_description_safety': 0.20,
    'permission_scope': 0.15,
    'supply_chain': 0.15,
    'community_signal': 0.15,
    'temporal_stability': 0.15
}

MIN_WEIGHT = 0.05
MAX_WEIGHT = 0.35
WEIGHT_ADJUSTMENT_STEP = 0.02
CORRELATION_THRESHOLD = 0.15


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_execute_url() -> str:
    return EXECUTE_URL


def get_query_url() -> str:
    return QUERY_URL


def get_db_path() -> str:
    return os.path.join(ZO_SENTINEL_PATH, "sentinel.duckdb")


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(get_query_url(), json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if isinstance(result, dict) and 'error' in result:
            logger.error(f"Query error: {result['error']}")
            return []
        return result if isinstance(result, list) else []
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return []


def ws_write(table: str, rows: Any) -> bool:
    payload = {"table": table, "rows": rows}
    try:
        resp = requests.post(get_write_url(), json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Write failed to {table}: {e}")
        return False


def ws_execute(sql: str, params: Optional[List[Any]] = None) -> bool:
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(get_execute_url(), json=payload, timeout=60)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Execute failed: {e}")
        return False


def send_heartbeat() -> bool:
    return ws_write("service_health", {
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.utcnow().isoformat(),
        "status": "running"
    })


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            if old_pid != os.getpid():
                try:
                    os.kill(old_pid, 0)
                    logger.warning(f"Another instance running with PID {old_pid}")
                    return False
                except OSError:
                    logger.info(f"Stale PID file, acquiring lock")
        except (ValueError, IOError) as e:
            logger.warning(f"Could not read PID file: {e}")
    try:
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
        return True
    except IOError as e:
        logger.error(f"Could not write PID file: {e}")
        return False


def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        logger.error(f"Could not remove PID file: {e}")


def ensure_mesh_events_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS mesh_events (
        id BIGINT PRIMARY KEY,
        event_type VARCHAR NOT NULL,
        source_service VARCHAR,
        server_id VARCHAR,
        payload TEXT,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """
    return ws_execute(sql)


def ensure_mcp_decisions_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_decisions (
        id BIGINT PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        system_verdict VARCHAR,
        final_verdict VARCHAR,
        analyst_id VARCHAR,
        analyst_override BOOLEAN DEFAULT FALSE,
        override_reason TEXT,
        decision_at TIMESTAMPTZ DEFAULT now(),
        signals_snapshot TEXT
    )
    """
    return ws_execute(sql)


def get_analyst_overrides(days_back: int = 30) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT 
        id,
        server_id,
        system_verdict,
        final_verdict,
        analyst_id,
        analyst_override,
        override_reason,
        decision_at,
        signals_snapshot
    FROM mcp_decisions
    WHERE analyst_override = TRUE
      AND decision_at >= now() - INTERVAL '{days_back} days'
    ORDER BY decision_at DESC
    """
    return ws_query(sql)


def parse_signals_from_snapshot(snapshot_str: str) -> Dict[str, float]:
    try:
        if not snapshot_str:
            return {}
        snapshot = json.loads(snapshot_str)
        if isinstance(snapshot, dict):
            return {k: float(v) for k, v in snapshot.items() if k in SIGNAL_NAMES}
        return {}
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning(f"Could not parse signals snapshot: {e}")
        return {}


def calculate_signal_accuracy(
    overrides: List[Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    signal_stats = {sig: {"correct": 0, "total": 0, "predictions": []} for sig in SIGNAL_NAMES}
    
    for record in overrides:
        final_verdict = record.get("final_verdict", "").upper()
        is_overridden_to_trusted = final_verdict in ("TRUSTED", "APPROVED", "SAFE")
        is_overridden_to_untrusted = final_verdict in ("UNTRUSTED", "BLOCKED", "MALICIOUS")
        
        signals = parse_signals_from_snapshot(record.get("signals_snapshot", ""))
        
        for sig in SIGNAL_NAMES:
            score = signals.get(sig, 0.5)
            signal_stats[sig]["total"] += 1
            
            if is_overridden_to_trusted and score >= 0.6:
                signal_stats[sig]["correct"] += 1
                signal_stats[sig]["predictions"].append(1)
            elif is_overridden_to_untrusted and score < 0.4:
                signal_stats[sig]["correct"] += 1
                signal_stats[sig]["predictions"].append(1)
            else:
                signal_stats[sig]["predictions"].append(0)
    
    accuracy = {}
    for sig, stats in signal_stats.items():
        if stats["total"] > 0:
            accuracy_rate = stats["correct"] / stats["total"]
            avg_correct = sum(stats["predictions"]) / len(stats["predictions"]) if stats["predictions"] else 0.5
            accuracy[sig] = {
                "accuracy_rate": accuracy_rate,
                "correct_count": stats["correct"],
                "total_count": stats["total"],
                "avg_confidence": avg_correct
            }
        else:
            accuracy[sig] = {
                "accuracy_rate": 0.5,
                "correct_count": 0,
                "total_count": 0,
                "avg_confidence": 0.5
            }
    
    return accuracy


def calculate_weight_adjustments(
    current_weights: Dict[str, float],
    signal_accuracy: Dict[str, Dict[str, Any]]
) -> Dict[str, float]:
    suggestions = {}
    
    sorted_by_accuracy = sorted(
        signal_accuracy.items(),
        key=lambda x: x[1]["accuracy_rate"],
        reverse=True
    )
    
    best_accuracy = sorted_by_accuracy[0][1]["accuracy_rate"]
    worst_accuracy = sorted_by_accuracy[-1][1]["accuracy_rate"]
    
    if best_accuracy - worst_accuracy < CORRELATION_THRESHOLD:
        logger.info("Signal accuracies are within threshold, no adjustments needed")
        return {}
    
    for sig, accuracy in signal_accuracy.items():
        accuracy_rate = accuracy["accuracy_rate"]
        current_weight = current_weights.get(sig, 0.15)
        baseline = 0.5
        
        diff_from_baseline = accuracy_rate - baseline
        
        if diff_from_baseline > CORRELATION_THRESHOLD:
            adjustment = min(diff_from_baseline * 0.1, WEIGHT_ADJUSTMENT_STEP)
            new_weight = min(current_weight + adjustment, MAX_WEIGHT)
            suggestions[sig] = round(new_weight, 3)
        elif diff_from_baseline < -CORRELATION_THRESHOLD:
            adjustment = min(abs(diff_from_baseline) * 0.1, WEIGHT_ADJUSTMENT_STEP)
            new_weight = max(current_weight - adjustment, MIN_WEIGHT)
            suggestions[sig] = round(new_weight, 0)
    
    total_suggested = sum(suggestions.values())
    if suggestions and abs(total_suggested - 1.0) > 0.01:
        scale_factor = 1.0 / total_suggested
        suggestions = {
            k: round(v * scale_factor, 3) for k, v in suggestions.items()
        }
    
    return suggestions


def generate_weight_suggestions_markdown(
    current_weights: Dict[str, float],
    suggestions: Dict[str, float],
    signal_accuracy: Dict[str, Dict[str, Any]],
    override_count: int,
    analysis_period_days: int
) -> str:
    md = f"# ZO-SENTINEL Weight Adjustment Suggestions\n\n"
    md += f"**Generated**: {datetime.utcnow().isoformat()}\n"
    md += f"**Analysis Period**: Last {analysis_period_days} days\n"
    md += f"**Analyst Overrides Analyzed**: {override_count}\n\n"
    
    md += "## IMPORTANT: Human Review Required\n\n"
    md += "These suggestions are generated automatically based on analyst feedback.\n"
    md += "**DO NOT automatically apply these changes without human review.**\n\n"
    
    md += "## Current vs Suggested Weights\n\n"
    md += "| Signal | Current | Suggested | Change | Accuracy | Samples |\n"
    md += "|--------|---------|-----------|--------|----------|--------|\n"
    
    for sig in SIGNAL_NAMES:
        current = current_weights.get(sig, 0.15)
        suggested = suggestions.get(sig, current)
        change = suggested - current
        change_str = f"{change:+.3f}" if change != 0 else "—"
        accuracy = signal_accuracy.get(sig, {}).get("accuracy_rate", 0)
        count = signal_accuracy.get(sig, {}).get("total_count", 0)
        md += f"| {sig} | {current:.3f} | {suggested:.3f} | {change_str} | {accuracy:.1%} | {count} |\n"
    
    md += "\n## Signal Performance Analysis\n\n"
    
    sorted_signals = sorted(signal_accuracy.items(), key=lambda x: x[1]["accuracy_rate"], reverse=True)
    
    for sig, stats in sorted_signals:
        accuracy = stats["accuracy_rate"]
        count = stats["total_count"]
        verdict = "HIGH"
        if accuracy >= 0.7:
            verdict = "HIGH"
        elif accuracy >= 0.5:
            verdict = "MEDIUM"
        else:
            verdict = "LOW"
        
        md += f"### {sig} ({verdict})\n"
        md += f"- Accuracy: {accuracy:.1%}\n"
        md += f"- Correct Predictions: {stats['correct_count']}/{count}\n"
        md += f"- Current Weight: {current_weights.get(sig, 0.15):.3f}\n"
        
        if sig in suggestions:
            md += f"- **Suggested Weight: {suggestions[sig]:.3f}**\n"
            diff = suggestions[sig] - current_weights.get(sig, 0.15)
            if diff > 0:
                md += f"- Recommendation: Increase weight (+{diff:.3f})\n"
            else:
                md += f"- Recommendation: Decrease weight ({diff:.3f})\n"
        else:
            md += "- Recommendation: No change\n"
        md += "\n"
    
    md += "## Recommended Actions\n\n"
    
    increases = [(s, v) for s, v in suggestions.items() if v > current_weights.get(s, 0.15)]
    decreases = [(s, v) for s, v in suggestions.items() if v < current_weights.get(s, 0.15)]
    
    if increases:
        md += "### Increase Weight For:\n"
        for sig, weight in sorted(increases, key=lambda x: x[1] - current_weights.get(x[0], 0.15), reverse=True):
            md += f"- **{sig}**: {current_weights.get(sig, 0.15):.3f} → {weight:.3f}\n"
        md += "\n"
    
    if decreases:
        md += "### Decrease Weight For:\n"
        for sig, weight in sorted(decreases, key=lambda x: current_weights.get(x[0], 0.15) - x[1], reverse=True):
            md += f"- **{sig}**: {current_weights.get(sig, 0.15):.3f} → {weight:.3f}\n"
        md += "\n"
    
    if not suggestions:
        md += "No significant weight adjustments recommended.\n"
        md += "All signals are performing within acceptable parameters.\n\n"
    
    md += "## Implementation Instructions\n\n"
    md += "1. Review the suggestions above\n"
    md += "2. Modify `signal_weights.py` with approved changes\n"
    md += "3. Run smoke tests to verify behavior\n"
    md += "4. Deploy updated configuration\n\n"
    
    md += "---\n"
    md += "*This file is auto-generated. Manual review is required before applying changes.*\n"
    
    return md


def write_weight_suggestions(md_content: str) -> bool:
    try:
        output_path = os.path.join(ZO_SENTINEL_PATH, WEIGHT_SUGGESTIONS_FILE)
        with open(output_path, 'w') as f:
            f.write(md_content)
        logger.info(f"Wrote weight suggestions to {output_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to write suggestions file: {e}")
        return False


def log_feedback_processed(
    override_count: int,
    suggestions_count: int,
    period_days: int
) -> bool:
    payload = {
        "event_type": "feedback_processed",
        "source_service": SERVICE_NAME,
        "payload": json.dumps({
            "overrides_analyzed": override_count,
            "suggestions_generated": suggestions_count,
            "period_days": period_days,
            "timestamp": datetime.utcnow().isoformat()
        })
    }
    return ws_write("mesh_events", payload)


def run_analysis_cycle(period_days: int = 30) -> Dict[str, Any]:
    logger.info(f"Starting analyst feedback analysis (period: {period_days} days)")
    
    ensure_mcp_decisions_table()
    ensure_mesh_events_table()
    
    overrides = get_analyst_overrides(days_back=period_days)
    
    if not overrides:
        logger.info("No analyst overrides found in analysis period")
        return {
            "status": "no_data",
            "overrides_analyzed": 0,
            "suggestions": {}
        }
    
    logger.info(f"Found {len(overrides)} analyst overrides to analyze")
    
    signal_accuracy = calculate_signal_accuracy(overrides)
    
    for sig, stats in sorted(signal_accuracy.items(), key=lambda x: x[1]["accuracy_rate"], reverse=True):
        logger.info(f"  {sig}: {stats['accuracy_rate']:.1%} accuracy ({stats['correct_count']}/{stats['total_count']})")
    
    suggestions = calculate_weight_adjustments(DEFAULT_WEIGHTS, signal_accuracy)
    
    if suggestions:
        logger.info("Weight adjustment suggestions:")
        for sig, weight in sorted(suggestions.items()):
            diff = weight - DEFAULT_WEIGHTS.get(sig, 0.15)
            logger.info(f"  {sig}: {DEFAULT_WEIGHTS.get(sig, 0.15):.3f} → {weight:.3f} ({diff:+.3f})")
    else:
        logger.info("No weight adjustments suggested")
    
    md_content = generate_weight_suggestions_markdown(
        DEFAULT_WEIGHTS,
        suggestions,
        signal_accuracy,
        len(overrides),
        period_days
    )
    
    write_weight_suggestions(md_content)
    
    log_feedback_processed(len(overrides), len(suggestions), period_days)
    
    return {
        "status": "success",
        "overrides_analyzed": len(overrides),
        "suggestions": suggestions,
        "signal_accuracy": signal_accuracy
    }


def heartbeat_loop():
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            logger.error(f"Heartbeat failed: {e}")
        time.sleep(HEARTBEAT_INTERVAL)


def run():
    logger.info(f"Starting {SERVICE_NAME} daemon")
    
    if not check_single_instance():
        logger.error("Another instance is running. Exiting.")
        sys.exit(1)
    
    try:
        ensure_mesh_events_table()
        ensure_mcp_decisions_table()
        
        while True:
            try:
                result = run_analysis_cycle(period_days=30)
                
                if result["status"] == "success":
                    logger.info(
                        f"Cycle complete: analyzed {result['overrides_analyzed']} overrides, "
                        f"generated {len(result['suggestions'])} suggestions"
                    )
                elif result["status"] == "no_data":
                    logger.info("No analyst overrides found in this cycle")
                
            except Exception as e:
                logger.error(f"Cycle failed: {e}")
            
            time.sleep(CYCLE_INTERVAL)
    
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        remove_pid_file()
        logger.info("Daemon stopped")


if __name__ == "__main__":
    run()