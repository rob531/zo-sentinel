#!/usr/bin/env python3
"""
Wire signal_analyser_v2 into the daemon supervision stack.

This script:
1. Backs up existing supervisord config with timestamp
2. Verifies signal_analyser_v2.py exists on disk
3. Checks idempotency - exits 0 if daemon already wired
4. Adds [program:sentinel_signal_analyser_v2] entry to config
5. Reloads supervisord with supervisorctl reread && update
"""

import os
import sys
import subprocess
from datetime import datetime

# Paths
SENTINEL_ROOT = "/home/workspace/zo_sentinel"
CONFIG_PATH = f"{SENTINEL_ROOT}/supervisord_sentinel_full.conf"
SIGNAL_ANALYSER_V2_PATH = f"{SENTINEL_ROOT}/signal_analyser_v2.py"
DAEMON_PROGRAM_NAME = "sentinel_signal_analyser_v2"
DAEMON_SECTION = f"[program:{DAEMON_PROGRAM_NAME}]"


def backup_config():
    """Backup existing config with UTC timestamp."""
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Config not found: {CONFIG_PATH}")
    
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    backup_path = f"{CONFIG_PATH}.bak.{timestamp}"
    
    with open(CONFIG_PATH, 'r') as f:
        content = f.read()
    
    with open(backup_path, 'w') as f:
        f.write(content)
    
    print(f"Backup created: {backup_path}")
    return backup_path


def verify_signal_analyser_v2():
    """Verify signal_analyser_v2.py exists before wiring."""
    if not os.path.exists(SIGNAL_ANALYSER_V2_PATH):
        raise FileNotFoundError(
            f"signal_analyser_v2.py not found at {SIGNAL_ANALYSER_V2_PATH}. "
            "Cannot wire a non-existent daemon."
        )
    print(f"Verified: {SIGNAL_ANALYSER_V2_PATH} exists")


def check_already_wired():
    """Idempotency check - return True if already wired."""
    with open(CONFIG_PATH, 'r') as f:
        content = f.read()
    return DAEMON_SECTION in content


def ensure_logs_directory():
    """Ensure logs directory exists."""
    logs_dir = f"{SENTINEL_ROOT}/logs"
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir, exist_ok=True)
        print(f"Created logs directory: {logs_dir}")


def add_daemon_entry():
    """Add the sentinel_signal_analyser_v2 program entry to config."""
    with open(CONFIG_PATH, 'r') as f:
        content = f.read()
    
    # Build the new entry
    new_entry = f"""
# heartbeat: every 60s
{DAEMON_SECTION}
command=python3 {SIGNAL_ANALYSER_V2_PATH}
autostart=true
autorestart=true
restartsecs=30
stdout_logfile={SENTINEL_ROOT}/logs/signal_analyser_v2.log
stderr_logfile={SENTINEL_ROOT}/logs/signal_analyser_v2.err.log
user=workspace
"""
    
    # Append to config
    with open(CONFIG_PATH, 'a') as f:
        f.write(new_entry)
    
    print(f"Added {DAEMON_SECTION} to config")


def reload_supervisord():
    """Reload supervisord configuration."""
    try:
        # Reread configuration
        result = subprocess.run(
            ["supervisorctl", "reread"],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            print(f"supervisorctl reread: {result.stderr.strip()}")
        
        # Update to apply changes
        result = subprocess.run(
            ["supervisorctl", "update"],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            print(f"supervisorctl update: {result.stderr.strip()}")
        else:
            print("Supervisord reloaded successfully")
            
    except subprocess.TimeoutExpired:
        print("Warning: supervisorctl timed out")
    except FileNotFoundError:
        print("Warning: supervisorctl not found - skipping reload")


def wire_signal_analyser_v2():
    """Main wiring function."""
    # Step 1: Backup config
    backup_config()
    
    # Step 2: Verify signal_analyser_v2.py exists
    verify_signal_analyser_v2()
    
    # Step 3: Idempotency check
    if check_already_wired():
        print(f"{DAEMON_SECTION} already wired - nothing to do")
        return
    
    # Step 4: Ensure logs directory exists
    ensure_logs_directory()
    
    # Step 5: Add daemon entry
    add_daemon_entry()
    
    # Step 6: Reload supervisord
    reload_supervisord()
    
    print(f"Successfully wired {DAEMON_PROGRAM_NAME} to supervisord")


if __name__ == '__main__':
    wire_signal_analyser_v2()
    
    # Acceptance test
    import os
    config_path = '/home/workspace/zo_sentinel/supervisord_sentinel_full.conf'
    assert os.path.exists(config_path), 'supervisord config missing'
    with open(config_path) as f:
        conf = f.read()
    assert '[program:sentinel_signal_analyser_v2]' in conf, 'daemon not wired'
    print("PASS: signal_analyser_v2 wired to supervisord")