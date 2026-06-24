# deps: 
"""overview_dashboard_api

Provides a simple API to retrieve an overview data dictionary for a given user.
The implementation is deliberately lightweight and does not perform any
network or database writes. It aggregates data from placeholder helper
functions that simulate fetching data from various internal sources.

The module includes a self‑test that runs when executed as a script.
"""

from __future__ import annotations

from typing import Dict, Any


def _fetch_user_profile(user_id: str) -> Dict[str, Any]:
    """Simulate fetching a user profile.

    In a real deployment this would query an internal service. Here we return
    a static dictionary to keep the function pure and side‑effect free.
    """
    # Dummy data – in practice this would be richer.
    return {"user_id": user_id, "name": "Test User", "role": "member"}


def _fetch_recent_activity(user_id: str) -> Dict[str, Any]:
    """Simulate fetching recent activity for the user.

    Returns a dictionary with a list of recent actions. The data is static
    but varies slightly based on the user_id to avoid being completely
    identical for every call.
    """
    activities = [
        {"type": "login", "timestamp": "2024-01-01T12:00:00Z"},
        {"type": "view", "item": "dashboard", "timestamp": "2024-01-01T12:05:00Z"},
    ]
    # Slight variation based on user_id hash – deterministic.
    if hash(user_id) % 2 == 0:
        activities.append({"type": "edit", "item": "profile", "timestamp": "2024-01-01T12:10:00Z"})
    return {"activities": activities}


def _fetch_system_metrics() -> Dict[str, Any]:
    """Simulate fetching system‑wide metrics.

    These metrics are not user‑specific, so they are static.
    """
    return {"cpu_usage": 42, "active_sessions": 128}


def get_overview_data(user_id: str) -> Dict[str, Any]:
    """Return an aggregated overview dictionary for the given ``user_id``.

    The function combines data from several internal helper functions. It is
    deliberately pure – no network calls, no DB writes, and no side effects.
    """
    if not isinstance(user_id, str) or not user_id:
        raise ValueError("user_id must be a non‑empty string")

    profile = _fetch_user_profile(user_id)
    activity = _fetch_recent_activity(user_id)
    metrics = _fetch_system_metrics()

    overview: Dict[str, Any] = {
        "profile": profile,
        "activity": activity,
        "system_metrics": metrics,
    }
    return overview


if __name__ == "__main__":
    # Self‑test harness
    test_user = "test_user"
    data = get_overview_data(test_user)
    assert isinstance(data, dict), "Result should be a dict"
    assert data, "Resulting dictionary should not be empty"
    # Basic sanity checks on expected keys
    for key in ("profile", "activity", "system_metrics"):
        assert key in data, f"Missing key {key} in overview data"
    print("PASS")
