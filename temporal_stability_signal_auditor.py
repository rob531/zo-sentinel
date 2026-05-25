import logging
import time
import requests
from datetime import datetime, timezone
from typing import Any, Dict, List

WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
SERVICE_NAME = 'temporal_stability_signal_auditor'
CYCLE_INTERVAL_SECONDS = 60

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s'
)
logger = logging.getLogger(SERVICE_NAME)


def get_utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def send_heartbeat() -> None:
    payload = {
        'table': 'service_health',
        'rows': {
            'service': SERVICE_NAME,
            'last_heartbeat': get_utc_now_iso()
        },
        'wait': True
    }
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
        resp.raise_for_status()
        logger.info('Heartbeat sent: %s', resp.json())
    except Exception as e:
        logger.warning('Failed to send heartbeat: %s', e)


def query_signal_scores() -> List[Dict[str, Any]]:
    query_body = {
        'table': 'mcp_signal_scores',
        'query': "SELECT score FROM mcp_signal_scores WHERE dimension = 'temporal_stability'",
        'wait': True
    }
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=query_body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get('result') or data.get('data') or data
        if isinstance(rows, list):
            return rows
        else:
            logger.error('Unexpected query response structure: %s', data)
            return []
    except Exception as e:
        logger.error('Failed to query mcp_signal_scores: %s', e)
        return []


def compute_audit(scores: List[Dict[str, Any]]) -> Dict[str, Any]:
    values = []
    for row in scores:
        val = row.get('score')
        if val is None:
            val = row.get('score_value')
        if val is not None:
            try:
                values.append(float(val))
            except (ValueError, TypeError):
                pass

    distinct = set(values)
    distinct_count = len(distinct)
    score_min = min(distinct) if distinct else None
    score_max = max(distinct) if distinct else None
    verdict = 'OK' if distinct_count > 20 else 'WEAK'

    return {
        'dimension': 'temporal_stability',
        'distinct_score_count': distinct_count,
        'score_min': score_min,
        'score_max': score_max,
        'verdict': verdict,
        'timestamp': get_utc_now_iso()
    }


def report_audit(audit: Dict[str, Any]) -> None:
    payload = {
        'table': 'signal_audit_results',
        'rows': audit,
        'wait': True
    }
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
        resp.raise_for_status()
        logger.info('Audit report sent: %s', resp.json())
    except Exception as e:
        logger.warning('Failed to report audit result: %s', e)


def cycle() -> None:
    logger.info('Starting temporal_stability signal audit cycle.')
    scores = query_signal_scores()
    audit = compute_audit(scores)
    logger.info('Audit result: %s', audit)
    report_audit(audit)
    send_heartbeat()


def run() -> None:
    logger.info('Starting %s daemon.', SERVICE_NAME)
    while True:
        try:
            cycle()
        except Exception as e:
            logger.exception('Unhandled exception in audit cycle: %s', e)
        time.sleep(CYCLE_INTERVAL_SECONDS)


if __name__ == '__main__':
    run()