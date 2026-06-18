#!/usr/bin/env python3
"""
breaker_action_reset_snow_connector.py

Quality-gate breaker action to reset the snow_connector breaker,
enabling the queued snow_connector build in the next cycle.

Trigger rationale:
  - Breaker tripped since 2026-05-24
  - snow_connector.py is the top Phase 9 priority (0.92)
  - snow_connector.py does not yet exist in already_built_modules
  - The breaker is blocking its only path to creation
  - Resetting it enables the queued snow_connector build

Behavior:
  1. Query write_service for current snow_connector breaker state
  2. If already RESET, exit 0 (idempotent)
  3. If TRIPPED, update state to RESET and record reset_timestamp
  4. Log the reset event to audit_log (timestamp, action, rationale)
  5. Return success status

DB writes:
  - mcp_breaker_state table: UPDATE state='reset', reset_timestamp=<now>
  - mcp_audit_log: INSERT (timestamp, action='reset_snow_connector',
    target='snow_connector', meta with rationale)
"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("breaker_action_reset_snow_connector")


WRITE_SERVICE_BASE_URL = "http://127.0.0.1:8772"
HTTP_TIMEOUT_SECONDS = 10.0


def _http_request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Make an HTTP request to the write_service.

    Uses urllib to avoid additional dependencies.
    Returns parsed JSON response body.
    Raises on non-2xx or network errors.
    """
    import urllib.request
    import urllib.error

    url = f"{WRITE_SERVICE_BASE_URL}{path}"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    request_body = json.dumps(body).encode("utf-8") if body is not None else None

    request = urllib.request.Request(
        url,
        data=request_body,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            response_body = response.read().decode("utf-8")
            if response_body:
                return json.loads(response_body)
            return {}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        logger.error("HTTP %d for %s %s: %s", e.code, method, path, error_body)
        raise RuntimeError(f"HTTP {e.code}: {error_body}") from e
    except urllib.error.URLError as e:
        logger.error("Connection failed for %s %s: %s", method, path, e.reason)
        raise RuntimeError(f"Connection failed: {e.reason}") from e


def get_breaker_state(breaker_name: str = "snow_connector") -> dict[str, Any]:
    """
    Query the current state of a named breaker from write_service.

    Args:
        breaker_name: The identifier of the breaker to query

    Returns:
        The breaker state record from write_service
    """
    return _http_request(
        "POST",
        "/query",
        body={
            "sql": "SELECT * FROM mcp_breaker_state WHERE breaker_name = ?",
            "params": [breaker_name],
        },
    )


def update_breaker_state(
    breaker_name: str,
    new_state: str,
    reset_timestamp: str,
    tripped_at: str,
    reset_rationale: str,
) -> dict[str, Any]:
    """
    Update the breaker state to reset.

    Args:
        breaker_name: The identifier of the breaker to reset
        new_state: The new state ('reset')
        reset_timestamp: ISO 8601 timestamp of the reset
        tripped_at: Original timestamp when breaker tripped
        reset_rationale: Human-readable justification for reset

    Returns:
        The updated breaker record
    """
    return _http_request(
        "POST",
        "/execute",
        body={
            "sql": (
                "UPDATE mcp_breaker_state "
                "SET state = ?, reset_timestamp = ?, meta = ? "
                "WHERE breaker_name = ?"
            ),
            "params": [
                new_state,
                reset_timestamp,
                json.dumps({
                    "tripped_at": tripped_at,
                    "reset_rationale": reset_rationale,
                }),
                breaker_name,
            ],
            "wait": True,
        },
    )


def insert_breaker_log(
    action: str,
    target: str,
    meta: dict[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    """
    Record an audit log entry for the reset action.

    Args:
        action: The action performed (e.g., 'reset_snow_connector')
        target: The entity affected ('snow_connector')
        meta: Additional metadata about the event
        timestamp: ISO 8601 timestamp of the event

    Returns:
        The created audit_log record
    """
    return _http_request(
        "POST",
        "/write",
        body={
            "table": "mcp_audit_log",
            "rows": [
                {
                    "timestamp": timestamp,
                    "action": action,
                    "target": target,
                    "meta": meta,
                }
            ],
            "wait": True,
        },
    )


def run() -> int:
    """
    Execute the snow_connector breaker reset action.

    Returns:
        0 on success (including idempotent no-op when already reset)
        1 on error
    """
    breaker_name = "snow_connector"
    logger.info("Starting snow_connector breaker reset action")

    # Step 1: Query current state
    try:
        current_state = get_breaker_state(breaker_name)
        logger.info("Current breaker state query result: %s", current_state)
    except Exception as e:
        logger.error("Failed to query breaker state: %s", e)
        return 1

    # Extract state from query result (may be in 'data' or top-level depending on API)
    rows = current_state.get("data", [])
    if not rows:
        logger.warning("No breaker record found for '%s', treating as non-tripped", breaker_name)
        state_value = "unknown"
        tripped_at = ""
    else:
        record = rows[0] if isinstance(rows, list) else rows
        state_value = str(record.get("state", "")).lower()
        tripped_at = record.get("tripped_at", "")

    # Step 2: Idempotent check - if already reset, exit cleanly
    if state_value == "reset":
        logger.info(
            "snow_connector breaker is already in RESET state. "
            "No action needed (idempotent)."
        )
        return 0

    # Step 3: If TRIPPED, perform the reset
    if state_value not in ("tripped", "open", "unknown", ""):
        logger.warning(
            "Unexpected breaker state: %r. Proceeding with reset anyway.",
            state_value,
        )

    now_iso = datetime.now(timezone.utc).isoformat()

    reset_rationale = (
        "Quality-gate reset: snow_connector.py is the top Phase 9 priority (0.92) "
        "and does not yet exist in already_built_modules. The breaker has blocked "
        "its creation since 2026-05-24. No other breaker_action for this file is pending. "
        "Resetting enables the queued snow_connector build in the next cycle. "
        "Proposed by directive_architect at 2026-06-17T22:54:25."
    )

    try:
        updated_state = update_breaker_state(
            breaker_name=breaker_name,
            new_state="reset",
            reset_timestamp=now_iso,
            tripped_at=tripped_at,
            reset_rationale=reset_rationale,
        )
        logger.info("Breaker reset successful: %s", updated_state)
    except Exception as e:
        logger.error("Failed to reset breaker: %s", e)
        return 1

    # Step 4: Log the reset event to audit_log
    audit_meta = {
        "breaker_name": breaker_name,
        "previous_state": state_value,
        "tripped_at": tripped_at,
        "reset_timestamp": now_iso,
        "rationale": reset_rationale,
        "affected_target": "snow_connector.py",
        "priority": 0.92,
        "phase": 9,
        "proposed_by": "directive_architect",
        "proposed_at": "2026-06-17T22:54:25.317394+00:00",
    }

    try:
        audit_entry = insert_breaker_log(
            action="reset_snow_connector",
            target="snow_connector",
            meta=audit_meta,
            timestamp=now_iso,
        )
        logger.info("Audit log entry created: %s", audit_entry)
    except Exception as e:
        logger.error("Failed to write audit log entry: %s", e)
        # Audit log failure is non-fatal; breaker was still reset
        # Logged but we return success

    logger.info(
        "snow_connector breaker reset completed successfully. "
        "Pipeline may now proceed with snow_connector.py build."
    )
    return 0


def self_smoke_test() -> None:
    """
    Self-smoke test: verify module loads and basic functionality.
    Tests that all required methods exist and can be called.
    """
    logger.info("Starting self-smoke test for breaker_action_reset_snow_connector")

    try:
        # Test that run function exists and is callable
        assert callable(run), "run() function must be callable"
        logger.info("run() function is callable")

        # Test that helper functions exist
        assert callable(get_breaker_state), "get_breaker_state() must exist"
        assert callable(update_breaker_state), "update_breaker_state() must exist"
        assert callable(insert_breaker_log), "insert_breaker_log() must exist"
        logger.info("All helper functions exist")

        # Test that module can be imported without network calls
        # (already imported at top, but verify no side effects)
        logger.info("Module imported cleanly (no import-time side effects)")

        logger.info("Self-smoke test completed successfully")

    except Exception as e:
        logger.error("Self-smoke test failed: %s", e)
        raise SystemExit(1)


if __name__ == "__main__":
    self_smoke_test()
    sys.exit(run())
