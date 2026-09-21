#!/usr/bin/env python3
"""
ZO-SENTINEL Anti-Entropy Stale Diagnostic Module

Diagnoses anti_entropy daemon staleness issues by:
1. Getting service health for anti_entropy daemon
2. Tailing log files for last activity
3. Checking work queue accumulation vs processing rate
4. Writing diagnostic results to service_diagnostics table
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
LOG_DIR = Path("/var/log/zo_sentinel")
ENTROPY_LOG = LOG_DIR / "anti_entropy.log"
STALENESS_THRESHOLD_HOURS = 4
STALENESS_THRESHOLD_MINUTES = 1


def get_service_health(service_name: str) -> dict[str, Any] | None:
    """Get service health from write_service by querying health table."""
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={
                'table': 'service_health',
                'query': 'SELECT * FROM service_health WHERE service = ?',
                'params': [service_name],
                'wait': True
            },
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        if result.get('rows') and len(result['rows']) > 0:
            return result['rows'][0]
        return None
    except Exception as e:
        logger.error(f"Failed to get service health for {service_name}: {e}")
        return None


def tail_log_file(log_path: Path, lines: int = 100) -> list[str]:
    """Tail the last N lines from the log file."""
    if not log_path.exists():
        logger.warning(f"Log file not found: {log_path}")
        return []
    
    try:
        with open(log_path, 'r') as f:
            all_lines = f.readlines()
            return all_lines[-lines:] if len(all_lines) >= lines else all_lines
    except Exception as e:
        logger.error(f"Failed to read log file {log_path}: {e}")
        return []


def parse_log_activity(log_lines: list[str]) -> dict[str, Any]:
    """Parse log lines to extract last activity timestamp and work items processed."""
    if not log_lines:
        return {
            'last_activity': None,
            'last_activity_iso': None,
            'work_items_processed_recent': 0,
            'work_items_queued_recent': 0
        }
    
    last_activity = None
    work_processed = 0
    work_queued = 0
    timestamp_pattern = re.compile(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})')
    processed_pattern = re.compile(r'processed|entropy.*complete|target.*checked', re.IGNORECASE)
    queued_pattern = re.compile(r'queue|adding.*target|scheduling|entropy.*check', re.IGNORECASE)
    
    for line in reversed(log_lines):
        ts_match = timestamp_pattern.search(line)
        if ts_match and last_activity is None:
            try:
                last_activity = datetime.fromisoformat(ts_match.group(1).replace(' ', 'T'))
            except ValueError:
                pass
        
        if processed_pattern.search(line):
            work_processed += 1
        if queued_pattern.search(line):
            work_queued += 1
    
    return {
        'last_activity': last_activity,
        'last_activity_iso': last_activity.isoformat() if last_activity else None,
        'work_items_processed_recent': work_processed,
        'work_items_queued_recent': work_queued
    }


def check_work_queue_accumulation() -> dict[str, Any]:
    """Check if anti_entropy work queue is accumulating faster than processing."""
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={
                'table': 'entropy_check_targets',
                'query': '''
                    SELECT 
                        COUNT(*) as total_targets,
                        SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                        SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END) as processing,
                        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                        SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
                    FROM entropy_check_targets
                ''',
                'wait': True
            },
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        
        if result.get('rows') and len(result['rows']) > 0:
            row = result['rows'][0]
            pending = row.get('pending', 0) or 0
            processing = row.get('processing', 0) or 0
            total = row.get('total_targets', 0) or 0
            
            accumulating = pending > 0 and processing == 0
            
            return {
                'total_targets': total,
                'pending': pending,
                'processing': processing,
                'accumulating': accumulating,
                'accumulation_rate': pending if accumulating else 0
            }
    except Exception as e:
        logger.error(f"Failed to check work queue: {e}")
    
    return {
        'total_targets': 0,
        'pending': 0,
        'processing': 0,
        'accumulating': False,
        'accumulation_rate': 0
    }


def check_staleness(health: dict[str, Any] | None, log_activity: dict[str, Any]) -> dict[str, Any]:
    """Determine if anti_entropy daemon is stale based on health and log data."""
    issues = []
    is_stale = False
    
    threshold = timedelta(hours=STALENESS_THRESHOLD_HOURS, minutes=STALENESS_THRESHOLD_MINUTES)
    now = datetime.utcnow()
    
    if health and 'last_heartbeat' in health:
        try:
            last_heartbeat = datetime.fromisoformat(health['last_heartbeat'].replace('Z', '+00:00').replace('+00:00', ''))
            elapsed = now - last_heartbeat
            
            if elapsed > threshold:
                is_stale = True
                issues.append(f"Heartbeat stale: {elapsed.total_seconds() / 3600:.1f} hours old")
        except (ValueError, TypeError) as e:
            logger.warning(f"Could not parse heartbeat: {e}")
    
    if not log_activity.get('last_activity_iso'):
        is_stale = True
        issues.append("No recent log activity detected")
    else:
        try:
            last_activity = datetime.fromisoformat(log_activity['last_activity_iso'].replace('Z', '+00:00').replace('+00:00', ''))
            elapsed = now - last_activity
            if elapsed > threshold:
                is_stale = True
                issues.append(f"Log activity stale: {elapsed.total_seconds() / 3600:.1f} hours old")
        except (ValueError, TypeError):
            pass
    
    return {
        'is_stale': is_stale,
        'threshold_hours': STALENESS_THRESHOLD_HOURS,
        'threshold_minutes': STALENESS_THRESHOLD_MINUTES,
        'issues': issues,
        'summary': '; '.join(issues) if issues else 'No staleness detected'
    }


def write_diagnostic_result(
    diagnostic_id: str,
    service: str,
    is_stale: bool,
    staleness_details: dict[str, Any],
    work_queue_details: dict[str, Any],
    log_activity_details: dict[str, Any],
    health_data: dict[str, Any] | None
) -> bool:
    """Write diagnostic result to service_diagnostics table."""
    try:
        diagnostic_rows = {
            diagnostic_id: {
                'service': service,
                'diagnostic_type': 'staleness',
                'is_stale': is_stale,
                'staleness_threshold_hours': STALENESS_THRESHOLD_HOURS,
                'staleness_threshold_minutes': STALENESS_THRESHOLD_MINUTES,
                'staleness_summary': staleness_details.get('summary'),
                'staleness_issues': json.dumps(staleness_details.get('issues', [])),
                'work_queue_pending': work_queue_details.get('pending', 0),
                'work_queue_processing': work_queue_details.get('processing', 0),
                'work_queue_accumulating': work_queue_details.get('accumulating', False),
                'log_last_activity': log_activity_details.get('last_activity_iso'),
                'log_work_processed': log_activity_details.get('work_items_processed_recent', 0),
                'log_work_queued': log_activity_details.get('work_items_queued_recent', 0),
                'health_last_heartbeat': health_data.get('last_heartbeat') if health_data else None,
                'health_status': health_data.get('status') if health_data else None,
                'checked_at': datetime.utcnow().isoformat(),
                'target_server_id': service
            }
        }
        
        response = requests.post(
            WRITE_SERVICE_URL,
            json={
                'table': 'service_diagnostics',
                'rows': diagnostic_rows,
                'wait': True
            },
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        
        if result.get('success') or result.get('status') == 'success':
            logger.info(f"Diagnostic result written: {diagnostic_id}")
            return True
        
        logger.warning(f"Unexpected write response: {result}")
        return False
        
    except Exception as e:
        logger.error(f"Failed to write diagnostic result: {e}")
        return False


def run() -> dict[str, Any]:
    """Main diagnostic execution for anti_entropy staleness."""
    diagnostic_id = f"anti_entropy_stale_{int(time.time())}"
    
    logger.info("=" * 60)
    logger.info(f"Starting anti_entropy staleness diagnostic: {diagnostic_id}")
    logger.info("=" * 60)
    
    health_data = get_service_health('anti_entropy')
    logger.info(f"Service health retrieved: {'found' if health_data else 'not found'}")
    
    log_lines = tail_log_file(ENTROPY_LOG, lines=100)
    logger.info(f"Log file tailed: {len(log_lines)} lines")
    
    log_activity = parse_log_activity(log_lines)
    logger.info(f"Log activity parsed: last={log_activity.get('last_activity_iso')}")
    
    work_queue = check_work_queue_accumulation()
    logger.info(f"Work queue status: pending={work_queue.get('pending')}, processing={work_queue.get('processing')}")
    
    staleness = check_staleness(health_data, log_activity)
    logger.info(f"Staleness check: is_stale={staleness['is_stale']}, issues={len(staleness.get('issues', []))}")
    
    if staleness['is_stale']:
        logger.warning("anti_entropy daemon is STALE")
        for issue in staleness.get('issues', []):
            logger.warning(f"  - {issue}")
    
    write_success = write_diagnostic_result(
        diagnostic_id=diagnostic_id,
        service='anti_entropy',
        is_stale=staleness['is_stale'],
        staleness_details=staleness,
        work_queue_details=work_queue,
        log_activity_details=log_activity,
        health_data=health_data
    )
    
    diagnostic_result = {
        'diagnostic_id': diagnostic_id,
        'service': 'anti_entropy',
        'is_stale': staleness['is_stale'],
        'staleness': staleness,
        'work_queue': work_queue,
        'log_activity': log_activity,
        'health_data': health_data,
        'write_success': write_success
    }
    
    logger.info("=" * 60)
    logger.info(f"Diagnostic complete. Stale={staleness['is_stale']}, Written={write_success}")
    logger.info("=" * 60)
    
    return diagnostic_result


if __name__ == '__main__':
    result = run()
    sys.exit(0 if not result['is_stale'] else 1)