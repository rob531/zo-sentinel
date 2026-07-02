#!/usr/bin/env python3
"""
verify_write_service_health_status.py

A tiny health‑check utility for the ``write_service``. It performs a
non‑disruptive write, immediately queries the written value and validates
that:

* the HTTP responses are received within a configurable timeout (default 5 s)
* the ``service_health`` table contains a recent heartbeat for the service
* no error entries for the service are present in the ``audit_log`` table

The script can be executed directly::

    $ python verify_write_service_health_status.py

and will exit with status ``0`` (PASS) or ``1`` (FAIL) while printing a short
human‑readable message.

The implementation is deliberately defensive – any unexpected exception is
treated as a failure and reported with a traceback for easier debugging.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, Tuple

import requests

# --------------------------------------------------------------------------- #
# Configuration helpers
# --------------------------------------------------------------------------- #


def _env_or_default(name: str, default: str) -> str:
    """Return the value of an environment variable or a default."""
    return os.getenv(name, default)


# Default configuration – can be overridden via CLI or environment variables.
DEFAULT_WRITE_URL = _env_or_default("WRITE_SERVICE_URL", "http://localhost:8000/write")
DEFAULT_QUERY_URL = _env_or_default("WRITE_SERVICE_URL", "http://localhost:8000/query")
DEFAULT_DB_PATH = _env_or_default("WRITE_SERVICE_DB", "./write_service.db")
DEFAULT_TIMEOUT = int(_env_or_default("WRITE_SERVICE_TIMEOUT", "5"))  # seconds
DEFAULT_HEARTBEAT_MAX_AGE = int(_env_or_default("WRITE_SERVICE_HEARTBEAT_MAX_AGE", "30"))  # seconds


# --------------------------------------------------------------------------- #
# Core health‑check logic
# --------------------------------------------------------------------------- #


def _post_write(url: str, payload: Dict[str, Any], timeout: int) -> Tuple[bool, str]:
    """Send a write request and return (success, details)."""
    try:
        start = time.time()
        resp = requests.post(url, json=payload, timeout=timeout)
        elapsed = time.time() - start
        if resp.status_code != 200:
            return False, f"Write returned status {resp.status_code} (took {elapsed:.2f}s)"
        # Expect a JSON body with at least an ``id`` field.
        try:
            data = resp.json()
            if "id" not in data:
                return False, f"Write response JSON missing 'id' field (took {elapsed:.2f}s)"
        except json.JSONDecodeError:
            return False, f"Write response not valid JSON (took {elapsed:.2f}s)"
        return True, f"Write succeeded (took {elapsed:.2f}s, id={data['id']})"
    except requests.RequestException as exc:
        return False, f"Write request failed: {exc}"


def _post_query(url: str, payload: Dict[str, Any], timeout: int) -> Tuple[bool, str]:
    """Send a query request and return (success, details)."""
    try:
        start = time.time()
        resp = requests.post(url, json=payload, timeout=timeout)
        elapsed = time.time() - start
        if resp.status_code != 200:
            return False, f"Query returned status {resp.status_code} (took {elapsed:.2f}s)"
        try:
            data = resp.json()
            if not data.get("found", False):
                return False, f"Query did not find the record (took {elapsed:.2f}s)"
        except json.JSONDecodeError:
            return False, f"Query response not valid JSON (took {elapsed:.2f}s)"
        return True, f"Query succeeded (took {elapsed:.2f}s)"
    except requests.RequestException as exc:
        return False, f"Query request failed: {exc}"


def _check_heartbeat(db_path: str, service_name: str, max_age_seconds: int) -> Tuple[bool, str]:
    """
    Verify that the ``service_health`` table contains a recent heartbeat for
    ``service_name`` (i.e. a ``last_seen`` timestamp no older than ``max_age_seconds``).
    """
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT last_seen FROM service_health
            WHERE service_name = ?
            ORDER BY last_seen DESC LIMIT 1
            """,
            (service_name,),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return False, f"No heartbeat entry found for service '{service_name}'."
        last_seen_str = row[0]
        # Assume ISO‑8601 format stored in the DB.
        last_seen = datetime.fromisoformat(last_seen_str)
        age = datetime.utcnow() - last_seen
        if age > timedelta(seconds=max_age_seconds):
            return (
                False,
                f"Heartbeat is stale (age {age.total_seconds():.1f}s > {max_age_seconds}s).",
            )
        return True, f"Heartbeat is fresh (age {age.total_seconds():.1f}s)."
    except Exception as exc:
        return False, f"Failed to query heartbeat: {exc}"


def _check_error_logs(db_path: str, service_name: str) -> Tuple[bool, str]:
    """
    Ensure that the ``audit_log`` table does **not** contain any entries with
    ``level = 'ERROR'`` for the given service.
    """
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM audit_log
            WHERE service_name = ? AND level = 'ERROR'
            """,
            (service_name,),
        )
        (error_count,) = cur.fetchone()
        conn.close()
        if error_count == 0:
            return True, "No error logs found."
        return False, f"Found {error_count} error log entry(ies)."
    except Exception as exc:
        return False, f"Failed to query audit_log: {exc}"


def run_health_check(
    write_url: str = DEFAULT_WRITE_URL,
    query_url: str = DEFAULT_QUERY_URL,
    db_path: str = DEFAULT_DB_PATH,
    timeout: int = DEFAULT_TIMEOUT,
    heartbeat_max_age: int = DEFAULT_HEARTBEAT_MAX_AGE,
    service_name: str = "write_service",
) -> Tuple[bool, str]:
    """
    Execute the full health‑check sequence.

    Returns
    -------
    (overall_success, details)
        ``overall_success`` is ``True`` only if *all* sub‑checks succeed.
        ``details`` is a multiline string describing each step.
    """
    steps = []

    # 1️⃣  Write a tiny payload – we use a random UUID so that the query can
    #     locate the exact record we just created.
    import uuid

    test_id = str(uuid.uuid4())
    write_payload = {"id": test_id, "value": "health‑check‑ping"}
    success, msg = _post_write(write_url, write_payload, timeout)
    steps.append(f"WRITE: {msg}")
    if not success:
        return False, "\n".join(steps)

    # 2️⃣  Query the same record.
    query_payload = {"id": test_id}
    success, msg = _post_query(query_url, query_payload, timeout)
    steps.append(f"QUERY: {msg}")
    if not success:
        return False, "\n".join(steps)

    # 3️⃣  Verify heartbeat.
    success, msg = _check_heartbeat(db_path, service_name, heartbeat_max_age)
    steps.append(f"HEARTBEAT: {msg}")
    if not success:
        return False, "\n".join(steps)

    # 4️⃣  Look for error logs.
    success, msg = _check_error_logs(db_path, service_name)
    steps.append(f"ERROR_LOGS: {msg}")
    if not success:
        return False, "\n".join(steps)

    # All checks passed.
    return True, "\n".join(steps)


# --------------------------------------------------------------------------- #
# CLI entry point
# --------------------------------------------------------------------------- #


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify health of the write_service by performing a write, query, "
        "heartbeat and error‑log check."
    )
    parser.add_argument(
        "--write-url",
        default=DEFAULT_WRITE_URL,
        help=f"URL of the write endpoint (default: {DEFAULT_WRITE_URL})",
    )
    parser.add_argument(
        "--query-url",
        default=DEFAULT_QUERY_URL,
        help=f"URL of the query endpoint (default: {DEFAULT_QUERY_URL})",
    )
    parser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help=f"Path to the SQLite DB used by write_service (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--heartbeat-max-age",
        type=int,
        default=DEFAULT_HEARTBEAT_MAX_AGE,
        help=(
            "Maximum age (seconds) for a heartbeat entry to be considered fresh. "
            f"Default: {DEFAULT_HEARTBEAT_MAX_AGE}"
        ),
    )
    parser.add_argument(
        "--service-name",
        default="write_service",
        help="Logical name of the service as stored in the DB tables.",
    )
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()

    try:
        ok, details = run_health_check(
            write_url=args.write_url,
            query_url=args.query_url,
            db_path=args.db_path,
            timeout=args.timeout,
            heartbeat_max_age=args.heartbeat_max_age,
            service_name=args.service_name,
        )
    except Exception:  # pragma: no cover – defensive catch‑all
        tb = traceback.format_exc()
        print(f"FAIL: Unexpected exception while performing health check:\n{tb}", file=sys.stderr)
        sys.exit(1)

    if ok:
        print("PASS: write_service is healthy")
        print(details)
        sys.exit(0)
    else:
        print("FAIL: write_service is unhealthy", file=sys.stderr)
        print(details, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()