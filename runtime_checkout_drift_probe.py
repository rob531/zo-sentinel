"""
Runtime drift detection probe for perspective configuration.
Compares current runtime state against persisted snapshot.
"""
from datetime import datetime
from typing import Optional

import requests

from app.db import get_session


_CURRENT_CONFIG: dict = {}


def _fetch_snapshot_via_write_service(perspective_id: str) -> Optional[dict]:
    """Fetch latest snapshot for perspective via write_service /query endpoint."""
    url = "http://127.0.0.1:8772/query"
    sql = """
        SELECT perspective_id, taken_at, config_snapshot
        FROM perspective_snapshots
        WHERE perspective_id = %s
        ORDER BY taken_at DESC
        LIMIT 1
    """
    response = requests.post(
        url,
        json={"query": sql, "params": [perspective_id]},
        timeout=10
    )
    result = response.json()
    if result.get("rows"):
        return result["rows"][0]
    return None


def _get_current_config() -> dict:
    """Get current in-memory configuration (override via monkeypatch in tests)."""
    return _CURRENT_CONFIG


def check_runtime_drift(perspective_id: str) -> dict:
    """
    Check for drift between current runtime config and latest persisted snapshot.

    Args:
        perspective_id: ID of the perspective to check.

    Returns:
        Dict with drift_detected (bool), differences (list), and timestamp (ISO8601).
    """
    snapshot = _fetch_snapshot_via_write_service(perspective_id)

    if snapshot is None:
        return {
            "drift_detected": False,
            "differences": [],
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

    snapshot_time = snapshot.get("taken_at")
    persisted_config = snapshot.get("config_snapshot") or {}
    current_config = _get_current_config()

    differences = []
    for field in persisted_config:
        if field not in current_config or current_config[field] != persisted_config[field]:
            differences.append(field)

    return {
        "drift_detected": bool(differences),
        "differences": differences,
        "timestamp": snapshot_time
    }


if __name__ == "__main__":
    import sys
    from unittest.mock import patch

    simulated_config = {
        "enabled_features": ["feature_a", "feature_b"],
        "max_retries": 3,
        "timeout_seconds": 30
    }

    with patch.dict(
        "runtime_checkout_drift_probe._CURRENT_CONFIG",
        simulated_config,
        clear=True
    ):
        with patch("runtime_checkout_drift_probe.requests.post") as mock_post:
            mock_response = mock_post.return_value
            mock_response.json.return_value = {"rows": []}

            result = check_runtime_drift("test-persp")

            assert "drift_detected" in result, "Result missing drift_detected key"
            assert isinstance(result["timestamp"], str), "timestamp should be string"
            try:
                datetime.fromisoformat(result["timestamp"].replace("Z", "+00:00"))
            except ValueError:
                assert False, "timestamp is not valid ISO8601"

    print("PASS")