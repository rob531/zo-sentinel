# deps: requests
"""daemon_health_status_report.py

Utility module that queries the write_service ``service_health`` table and returns
a list of health status dictionaries for each daemon.

Mirrors the structure of ``verdict_breakdown_api.py``: pure function, stdlib + requests only,
no DB writes, no app.db imports (service_health lives in the write_service store, not app PG).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from typing import List, Dict

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SERVICE_HEALTH_TABLE = "service_health"

# Thresholds (seconds) per daemon – taken verbatim from the task description.
THRESHOLD_MAP: Dict[str, int] = {
    "write_service": 300,
    "inference_router": 120,
    "manager_agent": 120,
    "pipeline_bridge": 120,
    "t2_consumer": 120,
    "zo_sentinel_builder": 600,
    "sentinel_directive_generator": 7500,
    "gate_scheduler": 60,
    "self_diagnostics": 600,
    "build_watcher_api": 600,
    "mcp_scanner": 14400,
    "signal_analyser": 120,
    "trust_synthesiser": 600,
    "threat_intel_ingestor": 600,
    "attestation_engine": 600,
    "rug_pull_monitor": 28800,
    "risk_ranker": 600,
    "world_article_feeder": 600,
    "data_velocity": 120,
    "anti_entropy": 14400,
    "wisdom_synthesiser": 14400,
    "gate_orchestrator": 14400,
}

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _query_service_health() -> List[Dict[str, str]]:
    """Query the write_service ``service_health`` table.

    Returns a list of rows where each row is a mapping with at least the keys
    ``service`` and ``last_heartbeat``. Raises ``RuntimeError`` on HTTP failure or
    malformed response.
    """
    payload = {"sql": f"SELECT service, status, last_heartbeat FROM {SERVICE_HEALTH_TABLE}", "params": []}
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"Failed to query write_service: {exc}") from exc
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError("write_service returned non-JSON response") from exc
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise RuntimeError("Malformed response from write_service: missing 'rows' list")
    return rows


def _parse_timestamp(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp returned by write_service.

    write_service emits timestamps as ``YYYY-MM-DDTHH:MM:SSZ`` (UTC ``Z`` suffix).
    ``datetime.fromisoformat`` in Python 3.11+ handles the ``Z`` directly.
    """
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def get_daemon_health_status() -> List[dict]:
    """Return a list of health dictionaries for each daemon.

    Each dictionary contains:
    * ``name``       – daemon name (from the ``service`` column).
    * ``age_seconds`` – seconds elapsed since the last heartbeat.
    * ``is_stale``   – True when ``age_seconds`` exceeds the per-daemon threshold.
    * ``status``     – raw ``status`` column value from the table.
    * ``threshold_seconds`` – the threshold that was applied.
    * ``last_heartbeat`` – original timestamp string from the DB.
    """
    rows = _query_service_health()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    results: List[dict] = []
    for row in rows:
        name = row.get("service")
        raw_ts = row.get("last_heartbeat")
        status = row.get("status", "unknown")
        if not name or not raw_ts:
            continue
        try:
            hb = _parse_timestamp(raw_ts)
        except Exception:
            continue
        age_seconds = (now - hb).total_seconds()
        threshold = THRESHOLD_MAP.get(name, 300)
        is_stale = age_seconds > threshold
        results.append({
            "name": name,
            "age_seconds": age_seconds,
            "is_stale": is_stale,
            "status": status,
            "threshold_seconds": threshold,
            "last_heartbeat": raw_ts,
        })
    return results


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    class _FakeResponse:
        def __init__(self, json_data: dict):
            self._json = json_data
        def raise_for_status(self):
            pass
        def json(self):
            return self._json

    # Seed timestamps: one fresh (0 s old), one stale (400 s old), one unknown daemon.
    _now = datetime(2026, 7, 20, 14, 10, 0, tzinfo=timezone.utc)
    _stale = _now - timedelta(seconds=400)

    def _fake_post(url, json, timeout):
        return _FakeResponse({
            "rows": [
                {"service": "write_service", "status": "healthy",  "last_heartbeat": _now.isoformat().replace("+00:00", "Z")},
                {"service": "inference_router", "status": "healthy", "last_heartbeat": _stale.isoformat().replace("+00:00", "Z")},
                {"service": "unknown_daemon",  "status": "unknown", "last_heartbeat": _now.isoformat().replace("+00:00", "Z")},
            ]
        })

    original_post = requests.post
    requests.post = _fake_post
    try:
        health = get_daemon_health_status()
        assert isinstance(health, list), "Result is not a list"
        assert len(health) >= 1, "No rows returned"
        required_keys = {"name", "age_seconds", "is_stale", "status", "threshold_seconds", "last_heartbeat"}
        for entry in health:
            missing = required_keys - set(entry.keys())
            assert not missing, f"Missing keys {missing} in entry {entry}"
        # Spot-check known daemon thresholds
        ws_entry = next((e for e in health if e["name"] == "write_service"), None)
        assert ws_entry is not None, "write_service row missing"
        assert ws_entry["threshold_seconds"] == 300
        assert ws_entry["is_stale"] is False, "fresh heartbeat should not be stale"
        ir_entry = next((e for e in health if e["name"] == "inference_router"), None)
        assert ir_entry is not None, "inference_router row missing"
        assert ir_entry["threshold_seconds"] == 120
        assert ir_entry["is_stale"] is True, "400 s old > 120 s threshold should be stale"
        print("PASS")
    except AssertionError as ae:
        print(f"FAIL: {ae}", file=sys.stderr)
        sys.exit(1)
    finally:
        requests.post = original_post
