#!/usr/bin/env python3
"""
Daemon wrapper for incident_webhook_dispatcher.py

Exposes incident webhook dispatcher as a runnable service with heartbeat.
Runs continuously, polls for new incidents every 120s.
"""

import os
import sys
import time
import signal
import logging
import threading
from datetime import datetime, timezone
from typing import Optional, List

import requests

# Try to import the existing incident_webhook_dispatcher module
try:
    from incident_webhook_dispatcher import dispatch_incidents
    DISPATCHER_AVAILABLE = True
except ImportError as e:
    DISPATCHER_AVAILABLE = False
    dispatch_incidents = None
    print(f"[{timestamp()}] WARNING: incident_webhook_dispatcher module not found: {e}")
    print(f"[{timestamp()}] Daemon will run without dispatch functionality until module is available")

# Configuration from environment
ZO_SENTINEL_API_URL = os.environ.get("ZO_SENTINEL_API_URL", "http://127.0.0.1:8781")
WEBHOOK_URLS_STR = os.environ.get("INCIDENT_WEBHOOK_URLS", "")
webhook_urls: List[str] = [url.strip() for url in WEBHOOK_URLS_STR.split(",") if url.strip()]

HEARTBEAT_INTERVAL = 60  # seconds
LOOP_INTERVAL = 120  # seconds

# Global state
running = False

# Heartbeat capture for self-test
_last_heartbeat_data: Optional[dict] = None
_heartbeat_lock = threading.Lock()


def timestamp() -> str:
    """Get current UTC timestamp string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def log(msg: str) -> None:
    """Log message with timestamp prefix to stdout."""
    print(f"[{timestamp()}] {msg}", flush=True)


def send_heartbeat(last_cycle_ts: str) -> bool:
    """
    POST heartbeat to write_service table=service_health.
    
    Returns True if heartbeat was sent successfully, False otherwise.
    """
    global _last_heartbeat_data
    
    payload = {
        "service": "incident_webhook_dispatcher",
        "status": "running",
        "meta": {
            "last_cycle": last_cycle_ts
        }
    }
    
    try:
        response = requests.post(
            f"{ZO_SENTINEL_API_URL}/write_service",
            json=payload,
            timeout=5
        )
        success = response.status_code == 200
        
        # Capture for self-test verification
        with _heartbeat_lock:
            _last_heartbeat_data = payload.copy()
        
        if success:
            log(f"Heartbeat sent: last_cycle={last_cycle_ts}")
        else:
            log(f"Heartbeat failed: status={response.status_code}")
        
        return success
    except Exception as e:
        log(f"Heartbeat error: {e}")
        # Still capture even on failure for debugging
        with _heartbeat_lock:
            _last_heartbeat_data = payload.copy()
        return False


def run_dispatch_cycle() -> None:
    """
    Execute one dispatch cycle: call dispatch_incidents() if available.
    Exceptions are caught and logged but do not propagate.
    """
    if not DISPATCHER_AVAILABLE:
        log("Dispatcher not available, skipping dispatch")
        return
    
    if not webhook_urls:
        log("No webhook URLs configured, skipping dispatch")
        return
    
    try:
        log("Calling dispatch_incidents()...")
        dispatch_incidents(ZO_SENTINEL_API_URL, webhook_urls)
        log("Dispatch completed successfully")
    except Exception as e:
        log(f"Dispatch error (isolated): {e}")


def signal_handler(signum, frame):
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    global running
    log("Received shutdown signal, shutting down...")
    running = False


def run() -> None:
    """
    Start the daemon loop.
    
    Loop structure:
    1. Sleep LOOP_INTERVAL (120s)
    2. Execute dispatch_incidents() 
    3. Fire heartbeat
    4. Repeat until stopped
    
    Heartbeat is fired on every cycle even if dispatch raises an exception.
    """
    global running
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    running = True
    
    log("Starting incident webhook dispatcher daemon...")
    log(f"Dispatcher module available: {DISPATCHER_AVAILABLE}")
    log(f"API URL: {ZO_SENTINEL_API_URL}")
    log(f"Webhook URLs: {webhook_urls}")
    log(f"Heartbeat interval: {HEARTBEAT_INTERVAL}s, Loop interval: {LOOP_INTERVAL}s")
    
    cycle_count = 0
    last_heartbeat_ts = ""
    
    while running:
        cycle_count += 1
        cycle_start = time.time()
        cycle_ts = timestamp()
        log(f"Cycle {cycle_count} started")
        
        # Sleep for loop interval (120s)
        time.sleep(LOOP_INTERVAL)
        
        if not running:
            break
        
        # Execute dispatch cycle (exception isolated)
        run_dispatch_cycle()
        
        if not running:
            break
        
        # Fire heartbeat every cycle
        send_heartbeat(cycle_ts)
        last_heartbeat_ts = cycle_ts
    
    log("incident_webhook_dispatcher daemon stopped")


def _self_test() -> None:
    """
    Self-test for the daemon.
    
    Validates:
    1. run() is callable
    2. Daemon starts and POSTs heartbeat within timeout
    3. SIGTERM causes clean exit
    """
    global _last_heartbeat_data
    
    print("\n=== Running self-test ===")
    
    # Import ourselves to test
    try:
        import incident_webhook_dispatcher_daemon as daemon_mod
    except ImportError:
        print("FAIL: Cannot import self")
        sys.exit(1)
    
    # Assert run is callable
    assert callable(getattr(daemon_mod, 'run', None)), "run() must be callable"
    print("OK: run() is callable")
    
    # Reset heartbeat capture
    _last_heartbeat_data = None
    
    # Track if shutdown message was logged
    shutdown_logged = [False]
    original_log = daemon_mod.log
    
    def tracking_log(msg):
        original_log(msg)
        if "shutting down" in msg.lower():
            shutdown_logged[0] = True
    
    daemon_mod.log = tracking_log
    
    # Start daemon in background thread
    daemon_thread = threading.Thread(target=daemon_mod.run, daemon=True)
    daemon_thread.start()
    
    # Wait for heartbeat with timeout
    heartbeat_received = False
    timeout = 3.0
    start_wait = time.time()
    
    while time.time() - start_wait < timeout:
        with _heartbeat_lock:
            if _last_heartbeat_data is not None:
                heartbeat_received = True
                print(f"OK: Heartbeat received: {_last_heartbeat_data}")
                break
        time.sleep(0.1)
    
    assert heartbeat_received, "FAIL: Heartbeat not received within timeout"
    
    # Send SIGTERM to trigger graceful shutdown
    print("Sending SIGTERM...")
    signal.raise_signal(signal.SIGTERM)
    
    # Wait for thread to exit
    daemon_thread.join(timeout=2.0)
    
    assert not daemon_thread.is_alive(), "FAIL: Daemon thread did not exit"
    assert shutdown_logged[0], "FAIL: Shutdown message not logged"
    print("OK: Clean shutdown verified")
    
    # Restore original log
    daemon_mod.log = original_log
    
    print("PASS\n")


if __name__ == "__main__":
    _self_test()
    run()