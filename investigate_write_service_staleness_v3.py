# deps: requests
"""
investigate_write_service_staleness_v3.py

This script investigates the staleness of the write_service daemon's heartbeat.
It attempts to gather status and logs from the write_service HTTP API and
reports any anomalies.

The script is safe to import (no side effects) and performs its work when
run() is called or when executed as a script.
"""

import logging
import sys
from typing import Any, Dict, Tuple

import requests

# Configure basic logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

BASE_URL = "http://127.0.0.1:8772"


def _fetch(endpoint: str) -> Tuple[bool, Any]:
    """Helper to GET JSON from the write_service.

    Returns a tuple (success, data_or_error).
    """
    url = f"{BASE_URL}{endpoint}"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        # Try to parse JSON, but fallback to text
        try:
            return True, resp.json()
        except Exception:
            return True, resp.text
    except Exception as e:
        return False, str(e)


def _log_result(name: str, success: bool, payload: Any) -> None:
    if success:
        logging.info("%s succeeded: %s", name, payload)
    else:
        logging.error("%s failed: %s", name, payload)


def run() -> Dict[str, Any]:
    """Gather health and logs from write_service.

    Returns a dictionary with the collected information.
    """
    results: Dict[str, Any] = {}

    # 1. Check health endpoint (commonly /health or /status)
    for endpoint in ["/health", "/status"]:
        success, data = _fetch(endpoint)
        _log_result(f"GET {endpoint}", success, data)
        results[endpoint] = {"success": success, "data": data}
        if success:
            # If we got a successful response, stop trying other health endpoints
            break

    # 2. Attempt to fetch recent logs (if the service provides a /logs endpoint)
    success, data = _fetch("/logs")
    _log_result("GET /logs", success, data)
    results["/logs"] = {"success": success, "data": data}

    # 3. Simple sanity check: if health indicates stale heartbeat
    health_info = results.get("/health") or results.get("/status")
    if health_info and health_info.get("success"):
        payload = health_info.get("data")
        # Expect payload to contain a 'heartbeat' timestamp ISO string
        heartbeat = None
        if isinstance(payload, dict):
            heartbeat = payload.get("heartbeat") or payload.get("last_heartbeat")
        if heartbeat:
            logging.info("Write service heartbeat timestamp: %s", heartbeat)
        else:
            logging.warning("Heartbeat timestamp not found in health payload.")
    else:
        logging.error("Unable to retrieve health information from write_service.")

    return results


if __name__ == "__main__":
    # Idempotency marker – if the script has already been executed successfully,
    # we simply exit with status 0.
    try:
        outcome = run()
        # Basic sanity: ensure we got some data back
        assert isinstance(outcome, dict)
        # At least one of the health checks should have succeeded
        health_keys = ["/health", "/status"]
        assert any(outcome.get(k, {}).get("success") for k in health_keys)
    except Exception as exc:
        logging.exception("Investigation failed: %s", exc)
        sys.exit(1)
    sys.exit(0)
