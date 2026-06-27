#!/usr/bin/env python3
"""
verify_write_service_heartbeat_robustness.py

Utility to verify that the write_service updates its heartbeat regularly.
It repeatedly queries the ``service_health`` endpoint and checks that the
``last_heartbeat`` field advances roughly every ``interval_seconds`` seconds.

The module does **not** access the database directly – it uses ``requests`` to
talk to the service’s HTTP API.
"""

from __future__ import annotations

import time
import datetime
import json
from typing import Dict, Any, Optional

import requests

# --------------------------------------------------------------------------- #
# Configuration (can be overridden by the caller if needed)
# --------------------------------------------------------------------------- #
DEFAULT_ENDPOINT = "http://localhost:8000/service_health"
# The JSON payload is expected to contain a key ``last_heartbeat`` whose value
# is either a Unix epoch (float/int) or an ISO‑8601 timestamp string.
# --------------------------------------------------------------------------- #


def _parse_timestamp(value: Any) -> Optional[float]:
    """
    Convert the ``last_heartbeat`` value returned by the service into a Unix
    timestamp (seconds since the epoch).  Returns ``None`` if parsing fails.
    """
    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        # Try ISO‑8601 first
        try:
            # Python 3.7+ supports fromisoformat, but it does not handle the
            # trailing 'Z' for UTC.  Replace it with +00:00.
            iso = value.rstrip("Z")
            if iso.endswith("+00:00"):
                dt = datetime.datetime.fromisoformat(iso)
            else:
                dt = datetime.datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                # Assume UTC if no timezone info is present
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.timestamp()
        except Exception:
            pass

        # Fallback: try to interpret as a plain float string
        try:
            return float(value)
        except Exception:
            pass

    return None


def verify_heartbeat_robustness(
    duration_seconds: int = 60,
    interval_seconds: int = 5,
    endpoint: str = DEFAULT_ENDPOINT,
) -> Dict[str, Any]:
    """
    Periodically query the write_service ``service_health`` endpoint and verify
    that the ``last_heartbeat`` field is updated roughly every ``interval_seconds``
    seconds for the whole ``duration_seconds`` period.

    Parameters
    ----------
    duration_seconds: int
        Total time to monitor the heartbeat.
    interval_seconds: int
        How often (in seconds) the endpoint is queried.
    endpoint: str
        URL of the service health endpoint.

    Returns
    -------
    dict
        {
            "success": bool,               # True if no missed heartbeats detected
            "missed_heartbeats": int,      # Number of intervals where the heartbeat lagged
            "average_delay": float,        # Mean observed interval between heartbeats (seconds)
            "details": List[dict]          # Optional per‑check diagnostics (useful for debugging)
        }
    """
    start_time = time.time()
    end_time = start_time + duration_seconds

    previous_ts: Optional[float] = None
    delays: list[float] = []
    missed = 0
    details: list[dict] = []

    # Tolerance: we allow a small jitter (e.g., 20% of the expected interval)
    tolerance = interval_seconds * 0.2

    while time.time() < end_time:
        check_time = time.time()
        try:
            resp = requests.get(endpoint, timeout=2)
            resp.raise_for_status()
            payload = resp.json()
            raw_ts = payload.get("last_heartbeat")
            current_ts = _parse_timestamp(raw_ts)

            if current_ts is None:
                raise ValueError(f"Unable to parse last_heartbeat value: {raw_ts!r}")

            if previous_ts is not None:
                delta = current_ts - previous_ts
                delays.append(delta)

                # If the delta exceeds the expected interval plus tolerance,
                # we count it as a missed heartbeat (or multiple missed heartbeats).
                if delta > interval_seconds + tolerance:
                    # Estimate how many heartbeats were missed.
                    # Example: expected 5 s, delta 12 s → 2 missed (12/5 ≈ 2.4 → 2)
                    missed_est = int(delta // interval_seconds) - 1
                    missed += max(missed_est, 1)

                details.append(
                    {
                        "check_time": datetime.datetime.fromtimestamp(check_time).isoformat(),
                        "last_heartbeat": datetime.datetime.fromtimestamp(current_ts).isoformat(),
                        "delta": delta,
                        "missed_estimated": int(delta // interval_seconds) - 1
                        if delta > interval_seconds + tolerance
                        else 0,
                    }
                )
            else:
                # First successful read – just record it.
                details.append(
                    {
                        "check_time": datetime.datetime.fromtimestamp(check_time).isoformat(),
                        "last_heartbeat": datetime.datetime.fromtimestamp(current_ts).isoformat(),
                        "delta": None,
                        "missed_estimated": 0,
                    }
                )

            previous_ts = current_ts

        except Exception as exc:  # noqa: BLE001 – we want to treat any failure as a miss
            # Network or parsing error – count as a missed heartbeat.
            missed += 1
            details.append(
                {
                    "check_time": datetime.datetime.fromtimestamp(check_time).isoformat(),
                    "error": str(exc),
                    "missed_estimated": 1,
                }
            )

        # Sleep until the next interval (accounting for time spent in the request)
        elapsed = time.time() - check_time
        sleep_time = max(0, interval_seconds - elapsed)
        time.sleep(sleep_time)

    average_delay = sum(delays) / len(delays) if delays else 0.0
    success = missed == 0

    return {
        "success": success,
        "missed_heartbeats": missed,
        "average_delay": average_delay,
        "details": details,
    }


# --------------------------------------------------------------------------- #
# Self‑test block
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    """
    Simple sanity check – when run against a healthy write_service the function
    should report zero missed heartbeats.  The script asserts this condition
    and prints ``PASS`` on success, otherwise ``FAIL`` with a short diagnostic.
    """
    result = verify_heartbeat_robustness()
    if result["missed_heartbeats"] == 0 and result["success"]:
        print("PASS")
    else:
        print("FAIL")
        print(json.dumps(result, indent=2))