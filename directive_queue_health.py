#!/usr/bin/env python3
"""
Directive Queue Health Daemon
Monitors pending directives and reports queue health metrics.
"""

import logging
import os
import time
import json
from datetime import datetime, timezone
from pathlib import Path
import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('directive_queue_health')

DIRECTIVES_PATH = Path('/home/workspace/zo_sentinel/directives')
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
CYCLE_INTERVAL = 300  # 5 minutes


def get_pending_directives():
    """Get list of pending directive files (.json that aren't .done.json or .failed.json)."""
    pending = []
    if not DIRECTIVES_PATH.exists():
        logger.warning(f"Directives path does not exist: {DIRECTIVES_PATH}")
        return pending
    
    try:
        for filepath in DIRECTIVES_PATH.glob('*.json'):
            name = filepath.name
            if name.endswith('.done.json') or name.endswith('.failed.json'):
                continue
            if filepath.is_file():
                pending.append(filepath)
    except Exception as e:
        logger.error(f"Error scanning directives directory: {e}")
    
    return pending


def get_queue_health():
    """Calculate queue health metrics."""
    pending_files = get_pending_directives()
    pending_count = len(pending_files)
    
    oldest_pending_age_secs = 0
    if pending_files:
        try:
            oldest_mtime = min(f.stat().st_mtime for f in pending_files)
            oldest_pending_age_secs = int(time.time() - oldest_mtime)
        except Exception as e:
            logger.error(f"Error calculating oldest pending age: {e}")
    
    return {
        'pending_count': pending_count,
        'oldest_pending_age_secs': oldest_pending_age_secs,
        'checked_at': datetime.now(timezone.utc).isoformat()
    }


def write_to_mesh_memory(health_data):
    """Write queue health data to mesh_memory via write_service."""
    payload = {
        'table': 'mesh_memory',
        'rows': {
            'agent_id': 'directive_queue_health',
            'memory_type': 'queue_state',
            'data': health_data,
            'created_at': datetime.now(timezone.utc).isoformat()
        },
        'wait': True
    }
    
    try:
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        response.raise_for_status()
        logger.info(f"Successfully wrote queue health: {health_data}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to write to mesh_memory: {e}")
        return False


def cycle():
    """Execute one health check cycle."""
    logger.info("Running directive queue health check...")
    
    health_data = get_queue_health()
    logger.info(f"Queue health metrics: {health_data}")
    
    write_to_mesh_memory(health_data)


def run():
    """Main daemon loop."""
    logger.info("Starting Directive Queue Health daemon...")
    logger.info(f"Monitoring: {DIRECTIVES_PATH}")
    logger.info(f"Cycle interval: {CYCLE_INTERVAL} seconds")
    
    while True:
        try:
            cycle()
        except Exception as e:
            logger.error(f"Error in health check cycle: {e}")
        
        time.sleep(CYCLE_INTERVAL)


if __name__ == '__main__':
    run()