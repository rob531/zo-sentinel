import requests
import time
import json
import os
from datetime import datetime, timezone, timedelta
import threading
from unittest.mock import patch, MagicMock

# --- Configuration ---
# Base URL for the write_service. Can be overridden by environment variable.
WRITE_SERVICE_URL = os.getenv("WRITE_SERVICE_URL", "http://localhost:8000")
# Name of this daemon service for heartbeats.
SERVICE_NAME = "mcp_risk_register_reconciliation_daemon"
# How often to send heartbeats to service_health (in seconds).
HEARTBEAT_INTERVAL_SECONDS = 30
# How often to run the full reconciliation logic (in seconds).
# This will be overridden for faster testing in the __main__ block.
RECONCILIATION_INTERVAL_SECONDS = 60

# --- Helper Functions for write_service interaction ---
def _post_to_write_service(endpoint: str, data: dict) -> dict:
    """
    Sends a POST request to the write_service.
    Args:
        endpoint: The API endpoint (e.g., "/insert", "/update").
        data: The JSON payload to send.
    Returns:
        The JSON response from the service, or an error dictionary.
    """
    url = f"{WRITE_SERVICE_URL}{endpoint}"
    try:
        response = requests.post(url, json=data, timeout=10)
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"[{_get_current_utc_timestamp()}] ERROR: POST to {url} failed: {e}")
        return {"error": str(e)}

def _get_from_write_service(endpoint: str, params: dict = None) -> dict:
    """
    Sends a GET request to the write_service.
    Args:
        endpoint: The API endpoint (e.g., "/query").
        params: Dictionary of query parameters.
    Returns:
        The JSON response from the service, or an error dictionary.
    """
    url = f"{WRITE_SERVICE_URL}{endpoint}"
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"[{_get_current_utc_timestamp()}] ERROR: GET from {url} failed: {e}")
        return {"error": str(e)}

def _send_heartbeat():
    """Sends a heartbeat to the service_health endpoint."""
    print(f"[{_get_current_utc_timestamp()}] INFO: Sending heartbeat...")
    _post_to_write_service(
        "/service_health/heartbeat",
        {"service_name": SERVICE_NAME, "status": "running"}
    )

def _get_current_utc_timestamp() -> str:
    """Returns the current UTC timestamp in ISO 8601 format (seconds precision)."""
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

# --- Reconciliation Logic ---
def _reconcile_risk_register():
    """
    Performs the core reconciliation logic for mcp_risk_register.
    1. Fetches active servers from `mcp_server_registry`.
    2. Fetches existing 'server_presence' risks from `mcp_risk_register`.
    3. For each active server, ensures a 'server_presence' risk entry exists.
       - If it exists, updates its `computed_at` timestamp to mark it as fresh.
       - If it doesn't exist, inserts a new 'server_presence' risk entry.
    """
    print(f"[{_get_current_utc_timestamp()}] INFO: Starting reconciliation cycle...")
    current_time = _get_current_utc_timestamp()

    # 1. Fetch active servers from mcp_server_registry
    server_registry_response = _get_from_write_service("/query", {"table": "mcp_server_registry"})
    if server_registry_response.get("error"):
        print(f"[{_get_current_utc_timestamp()}] ERROR: Failed to fetch mcp_server_registry: {server_registry_response['error']}")
        return
    
    #