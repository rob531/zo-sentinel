#!/usr/bin/env python3
"""
Utility to compute per‑axis scoring freshness lag.

The core function is pure and operates on a list of rows supplied by the caller.
Each row must contain:
    - server_id: identifier of the server
    - axis_name: one of the seven legal axis names
    - scored_at: ISO‑8601 timestamp string or a datetime instance

The function returns a dictionary with overall server count, per‑axis lag
statistics, and the axis with the worst (largest 95th percentile) lag.
"""

from __future__ import annotations

import datetime
import math
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------

LEGAL_AXES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}


def _to_datetime(value: Any) -> datetime.datetime:
    """Convert ISO‑8601 string or datetime to a timezone‑aware datetime (UTC)."""
    if isinstance(value, datetime.datetime):
        dt = value
    else:
        s = str(value)
        # Handle trailing Z (UTC) which fromisoformat does not accept
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(s)
    # Ensure timezone‑aware UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    else:
        dt = dt.astimezone(datetime.timezone.utc)
    return dt


def _percentile(data: List[float], pct: float) -> float:
    """Return the nearest‑rank percentile (pct in [0,100])."""
    if not data:
        return 0.0
    data_sorted = sorted(data)
    n = len(data_sorted)
    # Nearest‑rank method
    rank = math.ceil(pct / 100 * n)
    idx = max(rank - 1, 0)
    return float(data_sorted[idx])


def compute_axis_refresh_lag(
    rows: List[Dict[str, Any]], now: Optional[datetime.datetime] = None
) -> Dict[str, Any]:
    """
    Compute freshness lag statistics per axis.

    Parameters
    ----------
    rows:
        List of dictionaries with keys ``server_id``, ``axis_name`` and ``scored_at``.
    now:
        Reference time; defaults to ``datetime.datetime.utcnow()`` (UTC).

    Returns
    -------
    dict
        {
            "servers": int,
            "axes": {
                axis_name: {
                    "lag_seconds_p50": float,
                    "lag_seconds_p95": float,
                    "stale_over_24h": int,
                    "never_scored": int,
                },
                ...
            },
            "worst_axis": str | None,
        }
    """
    now = now or datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)

    # Gather distinct servers
    all_servers = {row["server_id"] for row in rows}
    total_servers = len(all_servers)

    # Latest score per (server, axis)
    latest: Dict[tuple, datetime.datetime] = {}
    axes_present: set[str] = set()

    for row in rows:
        axis = row["axis_name"]
        if axis not in LEGAL_AXES:
            continue  # ignore unknown axes silently
        axes_present.add(axis)
        server = row["server_id"]
        scored_at = _to_datetime(row["scored_at"])
        key = (server, axis)
        if key not in latest or scored_at > latest[key]:
            latest[key] = scored_at

    # Compute per‑axis statistics
    axes_stats: Dict[str, Dict[str, Any]] = {}
    worst_axis: Optional[str] = None
    worst_p95 = -1.0

    for axis in axes_present:
        # Gather lag seconds for servers that have a score for this axis
        lags: List[float] = []
        for server in all_servers:
            key = (server, axis)
            if key in latest:
                delta = now - latest[key]
                lag_seconds = delta.total_seconds()
                # Guard against negative lag (future timestamps)
                lag_seconds = max(lag_seconds, 0.0)
                lags.append(lag_seconds)

        p50 = _percentile(lags, 50)
        p95 = _percentile(lags, 95)
        stale_over_24h = sum(1 for v in lags if v > 86_400)
        never_scored = total_servers - len(lags)

        axes_stats[axis] = {
            "lag_seconds_p50": p50,
            "lag_seconds_p95": p95,
            "stale_over_24h": stale_over_24h,
            "never_scored": never_scored,
        }

        if p95 > worst_p95:
            worst_p95 = p95
            worst_axis = axis

    result: Dict[str, Any] = {
        "servers": total_servers,
        "axes": axes_stats,
        "worst_axis": worst_axis,
    }
    return result


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Synthetic test data
    now_dt = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)

    def iso(dt: datetime.datetime) -> str:
        return dt.isoformat().replace("+00:00", "Z")

    rows = [
        # overall_risk scores for three servers
        {
            "server_id": "s1",
            "axis_name": "overall_risk",
            "scored_at": iso(now_dt - datetime.timedelta(hours=1)),
        },
        {
            "server_id": "s2",
            "axis_name": "overall_risk",
            "scored_at": iso(now_dt - datetime.timedelta(days=3)),
        },
        {
            "server_id": "s3",
            "axis_name": "overall_risk",
            "scored_at": iso(now_dt - datetime.timedelta(hours=2)),
        },
        # auth_strength scores for two servers (s3 missing)
        {
            "server_id": "s1",
            "axis_name": "auth_strength",
            "scored_at": iso(now_dt - datetime.timedelta(hours=5)),
        },
        {
            "server_id": "s2",
            "axis_name": "auth_strength",
            "scored_at": iso(now_dt - datetime.timedelta(days=3, hours=1)),
        },
    ]

    result = compute_axis_refresh_lag(rows, now=now_dt)

    # Assertions per acceptance criteria
    assert result["servers"] == 3, "should detect three distinct servers"
    # Axis set must match axes present in input
    expected_axes = {"overall_risk", "auth_strength"}
    assert set(result["axes"].keys()) == expected_axes, "axes set mismatch"
    # never_scored for auth_strength should be 1 (s3 missing)
    assert result["axes"]["auth_strength"]["never_scored"] == 1, "never_scored count incorrect"
    # lag percentiles must be non‑negative
    for axis_stats in result["axes"].values():
        assert axis_stats["lag_seconds_p50"] >= 0, "p50 negative"
        assert axis_stats["lag_seconds_p95"] >= 0, "p95 negative"

    print("PASS")