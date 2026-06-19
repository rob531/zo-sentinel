#!/usr/bin/env python3
"""
Snow Connector Supervisord Wiring
Adds sentinel_snow_connector to supervisord and adds heartbeat to snow_connector.py
"""
import sys
sys.path.insert(0, '/home/workspace/zo_sentinel')

import os
import re
import time
import logging
import threading
import requests
from datetime import datetime

SERVICE_NAME = "snow_connector"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
HEARTBEAT_INTERVAL = 60  # seconds per spec section 6

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

SUPERVISORD_CONF = "/home/workspace/zo_sentinel/supervisord_sentinel_full.conf"
SNOW_CONNECTOR_PY = "/home/workspace/zo_sentinel/snow_connector.py"

SNOW_CONNECTOR_PROGRAM = """
[program:sentinel_snow_connector]
command=python3 /home/workspace/zo_sentinel/snow_connector.py
directory=/home/workspace/zo_sentinel
autostart=true
autorestart=true
user=workspace
startretries=5
stdout_logfile=/home/workspace/logs/sentinel_snow_connector.log
stderr_logfile=/home/workspace/logs/sentinel_snow_connector.log
stdout_logfile_maxbytes=5MB
"""


def send_heartbeat() -> bool:
    """Send heartbeat to service_health via write_service."""
    try:
        payload = {
            "service": SERVICE_NAME,
            "status": "alive",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "meta": {"pid": os.getpid()}
        }
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": "service_health", "rows": payload, "wait": True},
            timeout=10
        )
        return response.status_code == 200 and response.json().get("ok", False)
    except Exception as e:
        log.error(f"Heartbeat failed: {e}")
        return False


def heartbeat_loop():
    """Background thread: sends heartbeat every HEARTBEAT_INTERVAL seconds."""
    log.info(f"Starting heartbeat loop (interval={HEARTBEAT_INTERVAL}s)")
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


def add_heartbeat_to_snow_connector():
    """Add heartbeat functionality to snow_connector.py."""
    if not os.path.exists(SNOW_CONNECTOR_PY):
        log.error(f"Source file not found: {SNOW_CONNECTOR_PY}")
        return False
    
    with open(SNOW_CONNECTOR_PY, 'r') as f:
        content = f.read()
    
    # Check if heartbeat already exists
    if 'heartbeat_loop' in content and 'send_heartbeat' in content:
        log.info("snow_connector.py already has heartbeat functionality")
        return True
    
    # Backup original
    backup_path = f"{SNOW_CONNECTOR_PY}.bak.{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    with open(backup_path, 'w') as f:
        f.write(content)
    log.info(f"Backed up snow_connector.py to {backup_path}")
    
    # Add heartbeat functions and threading import if not present
    heartbeat_code = '''
import threading
import time
from datetime import datetime

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
HEARTBEAT_INTERVAL = 60


def send_heartbeat() -> bool:
    """Send heartbeat to service_health via write_service (no HTTP between peer daemons)."""
    try:
        payload = {
            "service": "snow_connector",
            "status": "alive",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "meta": {"pid": %d}
        }
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": "service_health", "rows": payload, "wait": True},
            timeout=10
        )
        return response.status_code == 200 and response.json().get("ok", False)
    except Exception:
        return False


def heartbeat_loop():
    """Background thread: sends heartbeat every HEARTBEAT_INTERVAL seconds."""
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

''' % os.getpid()
    
    # Find insertion point after imports
    import_end = content.find('logging.basicConfig')
    if import_end == -1:
        import_end = content.find('log = logging.getLogger')
    if import_end == -1:
        # Insert after last import
        import_end = content.rfind('\\n')
        while import_end > 0 and content[import_end] != '\\n':
            import_end -= 1
    
    if import_end > 0:
        content = content[:import_end] + heartbeat_code + '\\n' + content[import_end:]
    
    # Add heartbeat thread start in run() or main block
    if 'if __name__ == "__main__"' in content:
        # Insert heartbeat thread start before run()
        old_main = 'if __name__ == "__main__":\\n    run()'
        new_main = '''if __name__ == "__main__":
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    run()'''
        content = content.replace(old_main, new_main)
    
    with open(SNOW_CONNECTOR_PY, 'w') as f:
        f.write(content)
    
    log.info("Added heartbeat to snow_connector.py")
    return True


def update_supervisord_config():
    """Add sentinel_snow_connector program to supervisord config."""
    if not os.path.exists(SUPERVISORD_CONF):
        log.error(f"Supervisord config not found: {SUPERVISORD_CONF}")
        return False
    
    with open(SUPERVISORD_CONF, 'r') as f:
        content = f.read()
    
    # Check if already present
    if '[program:sentinel_snow_connector]' in content:
        log.info("sentinel_snow_connector already in supervisord config")
        return True
    
    # Backup
    backup_path = f"{SUPERVISORD_CONF}.bak.{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    with open(backup_path, 'w') as f:
        f.write(content)
    log.info(f"Backed up supervisord config to {backup_path}")
    
    # Append new program
    content = content.rstrip() + '\\n\\n' + SNOW_CONNECTOR_PROGRAM.strip() + '\\n'
    
    with open(SUPERVISORD_CONF, 'w') as f:
        f.write(content)
    
    log.info("Added sentinel_snow_connector to supervisord config")
    return True


def run():
    """Execute wiring: update supervisord config and add heartbeat to snow_connector.py."""
    log.info("Starting snow_connector supervisord wiring...")
    
    # Step 1: Add heartbeat to snow_connector.py
    if not add_heartbeat_to_snow_connector():
        log.error("Failed to add heartbeat to snow_connector.py")
        return False
    
    # Step 2: Update supervisord config
    if not update_supervisord_config():
        log.error("Failed to update supervisord config")
        return False
    
    log.info("Snow connector wiring complete")
    return True


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
