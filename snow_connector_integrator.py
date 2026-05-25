#!/usr/bin/env python3
"""
snow_connector_integrator.py
Integration module for snow_connector.py (built 2026-04-16) into approval_workflow.
Exposes check_snow_ticket_status(ticket_id: str) -> dict using write_service on :8772.
"""

import json
import time
from typing import Optional

import requests

# ServiceNow connector lives at snow_connector.py - import for wiring
try:
    from snow_connector import (
        get_ticket_status,
        create_snow_ticket,
        update_snow_ticket,
        validate_ticket_id,
    )
except ImportError:
    # Graceful fallback if snow_connector not yet deployed
    get_ticket_status = None
    create_snow_ticket = None
    update_snow_ticket = None
    validate_ticket_id = None

# Write service endpoint
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"

# Ticket status cache (in-memory for wiring, actual state persisted via write_service)
_ticket_cache: dict = {}
_cache_ttl_seconds = 30


def check_snow_ticket_status(ticket_id: str) -> dict:
    """
    Check ServiceNow ticket status via wired snow_connector.
    Persists state to write_service and returns structured status.

    Args:
        ticket_id: ServiceNow ticket ID (e.g., 'INC0012345')

    Returns:
        dict with keys: ticket_id, status, state, assignee, updated_at, cached
    """
    if not ticket_id:
        return {"error": "ticket_id required", "ticket_id": ticket_id}

    # Validate format if validator available
    if validate_ticket_id and not validate_ticket_id(ticket_id):
        return {"error": "invalid ticket_id format", "ticket_id": ticket_id}

    # Check cache first
    cached = _ticket_cache.get(ticket_id)
    if cached and (time.time() - cached.get("_cached_at", 0)) < _cache_ttl_seconds:
        return {**cached, "cached": True}

    # Fetch via snow_connector (actual SNOW API calls are internal to snow_connector.py)
    result = {
        "ticket_id": ticket_id,
        "status": "unknown",
        "state": "unknown",
        "assignee": None,
        "updated_at": None,
        "cached": False,
    }

    if get_ticket_status:
        try:
            sn_result = get_ticket_status(ticket_id)
            if sn_result.get("success"):
                result.update({
                    "status": sn_result.get("status", "unknown"),
                    "state": sn_result.get("state", "unknown"),
                    "assignee": sn_result.get("assignee"),
                    "updated_at": sn_result.get("sys_updated_on") or sn_result.get("updated_at"),
                    "snow_url": sn_result.get("snow_url"),
                    "cached": False,
                })
            elif sn_result.get("error"):
                result["error"] = sn_result.get("error")
        except Exception as e:
            result["error"] = f"snow_connector failure: {str(e)}"
    else:
        result["warning"] = "snow_connector not loaded"

    # Cache result
    result["_cached_at"] = time.time()
    _ticket_cache[ticket_id] = result.copy()

    # Persist to write_service for state tracking
    _persist_ticket_state(ticket_id, result)

    return result


def _persist_ticket_state(ticket_id: str, state: dict) -> None:
    """Write ticket state to write_service for audit trail."""
    try:
        payload = {
            "table": "snow_ticket_states",
            "rows": [{
                "ticket_id": ticket_id,
                "status": state.get("status", "unknown"),
                "state": state.get("state", "unknown"),
                "assignee": state.get("assignee"),
                "updated_at": state.get("updated_at"),
                "cached": 1 if state.get("cached") else 0,
                "logged_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }],
            "wait": True,
        }
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
    except Exception:
        # Non-critical: logging failure shouldn't break ticket check
        pass


def resolve_ticket(ticket_id: str, resolution: str, resolver: str = "system") -> dict:
    """
    Resolve a ServiceNow ticket via wired snow_connector.
    Only exposes wiring - actual SNOW API in snow_connector.py.
    """
    if not ticket_id:
        return {"error": "ticket_id required"}

    result = {"ticket_id": ticket_id, "resolved": False}

    if update_snow_ticket:
        try:
            update_result = update_snow_ticket(ticket_id, {
                "state": "Resolved",
                "close_notes": resolution,
                "resolved_by": resolver,
            })
            if update_result.get("success"):
                result["resolved"] = True
                result["resolution"] = resolution
                # Invalidate cache
                _ticket_cache.pop(ticket_id, None)
        except Exception as e:
            result["error"] = str(e)
    else:
        result["warning"] = "snow_connector update not available"

    return result


def get_ticket_audit_trail(ticket_id: str) -> list:
    """Query write_service for ticket state history."""
    try:
        query = f"SELECT * FROM snow_ticket_states WHERE ticket_id = '{ticket_id}' ORDER BY logged_at DESC LIMIT 50"
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": query}, timeout=10)
        if resp.ok:
            data = resp.json()
            return data.get("rows", [])
    except Exception:
        pass
    return []


if __name__ == "__main__":
    # Quick test
    test_id = "INC0012345"
    status = check_snow_ticket_status(test_id)
    print(f"Ticket {test_id}: {json.dumps(status, indent=2)}")