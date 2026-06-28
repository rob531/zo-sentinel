"""
FastAPI router that provides a health‑overview of all registered daemons.

GET /daemon_health/overview → List[Dict[str, Union[str, int]]]

The implementation queries the internal ``service_health`` table via the
HTTP endpoint ``http://127.0.0.1:8772/query`` (using *requests*).  No direct
database access is performed.

The response for each daemon contains:
* ``daemon_name`` – name of the daemon
* ``status`` – ``"healthy"`` if the last heartbeat is within the expected
  interval, otherwise ``"stale"``
* ``last_heartbeat_age_seconds`` – seconds elapsed since the last heartbeat
* ``expected_heartbeat_interval_seconds`` – the interval that is expected
  between heartbeats
"""

from __future__ import annotations

import time
from typing import List, Dict, Union

import requests
from fastapi import APIRouter, FastAPI, HTTPException

# --------------------------------------------------------------------------- #
# Router definition
# --------------------------------------------------------------------------- #

router = APIRouter()


@router.get(
    "/daemon_health/overview",
    response_model=List[Dict[str, Union[str, int]]],
    summary="Overview of daemon health status",
)
def daemon_health_overview() -> List[Dict[str, Union[str, int]]]:
    """
    Query the ``service_health`` table and transform the rows into a health‑overview.

    The internal query service expects a JSON payload with a ``sql`` key.
    The response is assumed to be JSON with a top‑level ``rows`` key that
    contains a list of dictionaries, each representing a row from the table.

    Expected columns in each row:
        - daemon_name (str)
        - last_heartbeat_timestamp (float) – Unix epoch seconds
        - expected_heartbeat_interval_seconds (int)

    Returns
    -------
    List[Dict[str, Union[str, int]]]
        A list where each element describes the health of a daemon.
    """
    query_url = "http://127.0.0.1:8772/query"
    payload = {"sql": "SELECT daemon_name, last_heartbeat_timestamp, expected_heartbeat_interval_seconds FROM service_health"}

    try:
        resp = requests.post(query_url, json=payload, timeout=5)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Unable to query health service: {exc}")

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Health service returned {resp.status_code}")

    data = resp.json()

    # The query service may return data in a few different shapes; we support the
    # most common ones.
    rows: List[Dict] = data.get("rows") or data.get("data") or data

    now = time.time()
    overview: List[Dict[str, Union[str, int]]] = []

    for row in rows:
        try:
            daemon_name = str(row["daemon_name"])
            last_hb = float(row["last_heartbeat_timestamp"])
            expected_interval = int(row["expected_heartbeat_interval_seconds"])
        except (KeyError, TypeError, ValueError) as exc:
            # Skip malformed rows but continue processing the rest.
            continue

        age_seconds = int(now - last_hb)
        status = "healthy" if age_seconds <= expected_interval else "stale"

        overview.append(
            {
                "daemon_name": daemon_name,
                "status": status,
                "last_heartbeat_age_seconds": age_seconds,
                "expected_heartbeat_interval_seconds": expected_interval,
            }
        )

    return overview


# --------------------------------------------------------------------------- #
# FastAPI app (for running as a service)
# --------------------------------------------------------------------------- #

app = FastAPI(title="Daemon Health Overview API")
app.include_router(router)


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    """
    Self‑test using FastAPI's TestClient.  The ``requests.post`` call is patched
    so that no real HTTP request is performed.
    """
    import json
    from unittest.mock import patch, Mock

    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Helper: build a fake response that mimics the real query service.
    # ------------------------------------------------------------------- #
    def _build_fake_response(rows: List[Dict]) -> Mock:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"rows": rows}
        return mock_resp

    # ------------------------------------------------------------------- #
    # Prepare deterministic test data.
    # ------------------------------------------------------------------- #
    _now = time.time()
    test_rows = [
        {
            "daemon_name": "write_service",
            "last_heartbeat_timestamp": _now - 120,  # 2 minutes ago → stale
            "expected_heartbeat_interval_seconds": 60,
        },
        {
            "daemon_name": "self_diagnostics",
            "last_heartbeat_timestamp": _now - 30,  # 30 seconds ago → healthy
            "expected_heartbeat_interval_seconds": 60,
        },
    ]

    # ------------------------------------------------------------------- #
    # Patch ``requests.post`` so the router receives the above rows.
    # ------------------------------------------------------------------- #
    with patch("requests.post", return_value=_build_fake_response(test_rows)):
        client = TestClient(app)

        # 1️⃣ 200 OK
        response = client.get("/daemon_health/overview")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        # 2️⃣  non‑empty JSON list
        body = response.json()
        assert isinstance(body, list) and len(body) > 0, "Response body must be a non‑empty list"

        # 3️⃣  each item contains required keys
        required_keys = {
            "daemon_name",
            "status",
            "last_heartbeat_age_seconds",
            "expected_heartbeat_interval_seconds",
        }
        for item in body:
            assert isinstance(item, dict), "Each list element must be a dict"
            missing = required_keys - item.keys()
            assert not missing, f"Missing keys in item: {missing}"

        # 4️⃣  at least one daemon reported as stale (write_service)
        stale_daemons = [d for d in body if d["status"] == "stale"]
        assert stale_daemons, "At least one daemon should be reported as stale"
        # Verify that the stale daemon is indeed the one we expect.
        stale_names = {d["daemon_name"] for d in stale_daemons}
        assert "write_service" in stale_names, "write_service should be stale in the test data"

        # If we reach here, everything passed.
        print("PASS")