#!/usr/bin/env python3
"""
ZO-SENTINEL Phase 3: Trust Synthesiser
T3 ZOMesh agent. Computes composite weighted trust_score from mcp_signal_scores,
maps to verdict taxonomy, writes verdict + reasoning to mcp_server_registry.
"""

import os
import sys
import time
import hashlib
import logging
import sqlite3
import subprocess
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple
import fcntl

import requests

# Configuration
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8772/execute'
QUERY_URL = 'http://127.0.0.1:8772/query'
HEARTBEAT_INTERVAL = 300  # 5 minutes
CYCLE_INTERVAL = 1800  # 30 minutes
LOG_DIR = '/home/workspace/zo_sentinel/logs'
DATA_DIR = '/home/workspace/zo_sentinel/data'

# Trust score weights
WEIGHTS = {
    'domain_trust': 0.20,
    'tool_description_safety': 0.20,
    'permission_scope': 0.15,
    'supply_chain': 0.15,
    'community_signal': 0.15,
    'temporal_stability': 0.15
}

# Verdict thresholds
VERDICT_THRESHOLDS = [
    (75, 'TRUSTED_GENERAL'),
    (60, 'TRUSTED_RESEARCH'),
    (45, 'ENTERPRISE_CONTROLLED'),
    (30, 'CAUTION_LIMITED'),
    (15, 'HIGH_RISK_ISOLATED'),
    (0, 'KNOWN_THREAT')
]

# Logging setup
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'{LOG_DIR}/trust_synthesiser.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def check_single_instance():
    """Acquire exclusive flock on /tmp/trust_synthesiser.lock. Exit on collision.

    Replaces the previous pgrep-based check which produced false positives
    whenever ANY other process had the script name in its command line
    (tail -f on the log, editors, grep, etc.). The flock is kernel-enforced
    and released automatically on process exit -- no stale PID files.
    Returned lock-file fd is kept alive by module-level reference.
    """
    lock_path = '/tmp/trust_synthesiser.lock'
    try:
        fd = open(lock_path, 'w')
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(__import__("os").getpid()))
        fd.flush()
        globals()['_single_instance_lock_fd'] = fd
        return True
    except (IOError, OSError):
        # Another instance holds the lock -- exit immediately.
        print(f"[trust_synthesiser] Another instance holds lock at {lock_path} -- exiting", flush=True)
        sys.exit(0)


def send_heartbeat(service_name: str) -> None:
    """Send heartbeat to service_health table."""
    try:
        requests.post(WRITE_SERVICE_URL, json={
            'table': 'service_health',
            'rows': {
                'service': service_name,
                'last_heartbeat': datetime.now(timezone.utc).isoformat()
            },
            'wait': True
        }, timeout=5)
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")


def get_write_url() -> str:
    """Get the write service URL."""
    return WRITE_SERVICE_URL


def get_db_path() -> str:
    """Get the path to the DuckDB database."""
    return os.path.join(DATA_DIR, 'zo_sentinel.db')


def query_signal_scores() -> List[Dict[str, Any]]:
    """Query all MCP signal scores from DuckDB."""
    sql = """
    SELECT
        server_id AS tool_name,
        MAX(CASE WHEN signal_name='domain_trust'            THEN score END) AS domain_trust,
        MAX(CASE WHEN signal_name='tool_description_safety' THEN score END) AS tool_description_safety,
        MAX(CASE WHEN signal_name='permission_scope'        THEN score END) AS permission_scope,
        MAX(CASE WHEN signal_name='supply_chain'            THEN score END) AS supply_chain,
        MAX(CASE WHEN signal_name='community_signal'        THEN score END) AS community_signal,
        MAX(CASE WHEN signal_name='temporal_stability'      THEN score END) AS temporal_stability,
        MAX(scored_at)                                                       AS last_updated
    FROM mcp_signal_scores
    GROUP BY server_id
    """
    try:
        response = requests.post(
            QUERY_URL,
            json={'sql': sql},
            timeout=35
        )
        response.raise_for_status()
        result = response.json()
        if 'rows' in result and result['rows']:
            return result['rows']
        if 'results' in result and result['results']:
            return result['results']
        return []
    except Exception as e:
        logger.error(f"Failed to query signal scores: {e}")
        return []


def compute_composite_score(signals: Dict[str, Optional[float]]) -> Tuple[float, List[str]]:
    """
    Compute weighted composite trust score from signal scores.
    Returns (score, list_of_missing_signals)
    """
    total_weight = 0.0
    weighted_sum = 0.0
    missing_signals = []
    
    for signal_name, weight in WEIGHTS.items():
        value = signals.get(signal_name)
        if value is not None and value >= 0:
            weighted_sum += value * weight
            total_weight += weight
        else:
            missing_signals.append(signal_name)
    
    if total_weight == 0:
        return 0.0, missing_signals
    
    # Normalize to account for missing signals
    # If we have all signals, max possible is 100
    # Scale factor ensures full signals = 100
    composite = (weighted_sum / total_weight) * (total_weight / sum(WEIGHTS.values()))
    return round(min(100.0, max(0.0, composite)), 2), missing_signals


def determine_verdict(score: float, missing_signals: List[str]) -> str:
    """Determine verdict based on trust score and available signals."""
    if len(missing_signals) >= 4:
        return 'INSUFFICIENT'
    
    for threshold, verdict in VERDICT_THRESHOLDS:
        if score > threshold:
            return verdict
    
    return 'KNOWN_THREAT'


def compute_confidence(score: float, missing_signals: List[str]) -> float:
    """
    Compute confidence level for the verdict.
    More signals available = higher confidence.
    Full signals available with score > 75 = highest confidence.
    """
    signals_present = len(WEIGHTS) - len(missing_signals)
    signal_coverage = signals_present / len(WEIGHTS)
    
    base_confidence = signal_coverage * 0.7  # Max 0.7 from coverage
    
    # Add confidence based on extremity of score (very high or very low = more confident)
    extremity = abs(score - 50) / 50
    base_confidence += extremity * 0.3
    
    return round(min(1.0, max(0.0, base_confidence)), 2)


def _signal_value(signals: Dict[str, Optional[float]], name: str) -> Optional[float]:
    """None-safe signal accessor: returns None when the signal is absent or unscored."""
    value = signals.get(name)
    return value if isinstance(value, (int, float)) else None


def generate_reasoning(
    tool_name: str,
    score: float,
    verdict: str,
    signals: Dict[str, Optional[float]],
    missing_signals: List[str]
) -> str:
    """
    Generate natural-language reasoning explaining the verdict.
    Concise, 2-3 sentences, suitable for InfoSec analyst review.
    """
    reasoning_parts = []
    
    # Base verdict description
    verdict_map = {
        'TRUSTED_GENERAL': 'Likely safe for general enterprise use',
        'TRUSTED_RESEARCH': 'Likely safe for research and exploratory use',
        'ENTERPRISE_CONTROLLED': 'Acceptable with documented security controls',
        'CAUTION_LIMITED': 'Use with caution, requires additional review',
        'HIGH_RISK_ISOLATED': 'High risk, limited to sandboxed environments',
        'KNOWN_THREAT': 'Known security threat, do not deploy',
        'INSUFFICIENT': 'Insufficient signal data for reliable assessment'
    }
    
    reasoning_parts.append(verdict_map.get(verdict, f'Verdict: {verdict}'))
    
    # Key signal highlights
    positive_signals = []
    negative_signals = []
    
    _dt = _signal_value(signals, 'domain_trust')
    if _dt is not None and _dt >= 70:
        positive_signals.append('strong domain trust')
    elif _dt is not None and _dt < 30:
        negative_signals.append('weak domain trust')
    
    _tds = _signal_value(signals, 'tool_description_safety')
    if _tds is not None and _tds >= 70:
        positive_signals.append('clear safety documentation')
    elif _tds is not None and _tds < 40:
        negative_signals.append('unclear safety profile')
    
    _ps = _signal_value(signals, 'permission_scope')
    if _ps is not None and _ps >= 80:
        positive_signals.append('minimal permissions')
    elif _ps is not None and _ps < 50:
        negative_signals.append('broad permission scope')
    
    _sc = _signal_value(signals, 'supply_chain')
    if _sc is not None and _sc >= 70:
        positive_signals.append('verified supply chain')
    elif _sc is not None and _sc < 40:
        negative_signals.append('unverified supply chain')
    
    if positive_signals:
        reasoning_parts.append(f'Strengths: {", ".join(positive_signals[:2])}.')
    
    if negative_signals:
        reasoning_parts.append(f'Concerns: {", ".join(negative_signals[:2])}.')
    
    # Missing signals note
    if missing_signals:
        reasoning_parts.append(f'Data gaps: {", ".join(missing_signals[:2])} not evaluated.')
    
    reasoning_parts.append(f'Composite score: {score}/100.')
    
    return ' '.join(reasoning_parts)


def write_verdict_to_registry(
    tool_name: str,
    trust_score: float,
    verdict: str,
    reasoning: str,
    confidence: float
) -> bool:
    """Write verdict data to mcp_server_registry via write_service."""
    now = datetime.now(timezone.utc).isoformat()
    
    data = {
        'server_id': tool_name,
        'trust_score': trust_score,
        'verdict': verdict,
        'verdict_reasoning': reasoning,
        'confidence': confidence,
        'last_assessed': now
    }
    
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={
                'table': 'mcp_server_registry',
                'rows': data,
                'wait': True
            },
            timeout=10
        )
        response.raise_for_status()
        logger.info(f"Written verdict for {tool_name}: {verdict} ({trust_score})")
        return True
    except Exception as e:
        logger.error(f"Failed to write verdict for {tool_name}: {e}")
        return False


def run_cycle() -> int:
    """Run one assessment cycle. Returns number of tools assessed."""
    logger.info("Starting trust synthesis cycle")
    send_heartbeat('trust_synthesiser')
    
    try:
        signal_scores = query_signal_scores()
        if not signal_scores:
            logger.info("No MCP signal scores found in database")
            send_heartbeat('trust_synthesiser')
            return 0
        
        assessed_count = 0
        
        for record in signal_scores:
            try:
                tool_name = record.get('tool_name')
                if not tool_name:
                    continue
                
                signals = {
                    'domain_trust': record.get('domain_trust'),
                    'tool_description_safety': record.get('tool_description_safety'),
                    'permission_scope': record.get('permission_scope'),
                    'supply_chain': record.get('supply_chain'),
                    'community_signal': record.get('community_signal'),
                    'temporal_stability': record.get('temporal_stability')
                }
                
                # Check if all signals are None/negative
                all_missing = all(v is None or v < 0 for v in signals.values())
                
                if all_missing:
                    score = 0.0
                    missing = list(WEIGHTS.keys())
                    verdict = 'INSUFFICIENT'
                else:
                    score, missing = compute_composite_score(signals)
                    verdict = determine_verdict(score, missing)
                
                confidence = compute_confidence(score, missing)
                reasoning = generate_reasoning(tool_name, score, verdict, signals, missing)
                
                write_verdict_to_registry(tool_name, score, verdict, reasoning, confidence)
                assessed_count += 1
                
            except Exception as e:
                tool_name = record.get('tool_name', 'unknown')
                logger.error(f"Failed to assess {tool_name}: {e}")
                continue
        
        logger.info(f"Completed cycle: assessed {assessed_count} tools")
        send_heartbeat('trust_synthesiser')
        return assessed_count
        
    except Exception as e:
        logger.error(f"Cycle failed: {e}")
        send_heartbeat('trust_synthesiser')
        return 0


def run() -> None:
    """Main daemon run loop."""
    proc_name = 'trust_synthesiser.py'
    
    check_single_instance()
    
    logger.info("Starting Trust Synthesiser daemon")
    send_heartbeat('trust_synthesiser')
    
    # Initial run on startup
    run_cycle()
    
    while True:
        time.sleep(CYCLE_INTERVAL)
        try:
            run_cycle()
        except Exception as e:
            logger.error(f"Unexpected error in run loop: {e}")
            time.sleep(60)


if __name__ == '__main__':
    run()