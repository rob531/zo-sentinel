import datetime
from typing import List, Dict

import requests
from fastapi import Depends

from app.db import get_session
from app.models import McpServerRegistry  # noqa: F401  (imported for side‑effects / type checking)


def _fetch_audit_events(
    server_id: int,
    start: datetime.datetime,
    end: datetime.datetime,
) -> List[Dict]:
    """
    Retrieve audit_log rows for a given server between ``start`` and ``end``.
    In production this issues a POST to the write‑service query endpoint.
    """
    sql = """
        SELECT server_id, timestamp, target_server_id, action, actor, detail
        FROM audit_log
        WHERE server_id = :sid
          AND timestamp >= :start_ts
          AND timestamp <= :end_ts
        ORDER BY timestamp ASC
    """
    payload = {
        "sql": sql,
        "params": {
            "sid": server_id,
            "start_ts": start.isoformat(),
            "end_ts": end.isoformat(),
        },
    }
    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json=payload,
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json().get("rows", [])


def query(
    server_id: int,
    days: int = 30,
    db=Depends(get_session),  # noqa: ARG001  (dependency placeholder – not used directly)
) -> Dict:
    """
    Build a per‑day audit event timeline for ``server_id`` over the past ``days`` days.

    Returns a dict:
    {
        "server_id": int,
        "days": int,
        "total_events": int,
        "series": [
            {
                "date": "YYYY‑MM‑DD",
                "event_count": int,
                "event_types": [str, ...],
                "actors": [str, ...],
            },
            ...
        ],
    }
    """
    # Determine date range (inclusive)
    today = datetime.datetime.utcnow().date()
    start_date = today - datetime.timedelta(days=days - 1)
    start_dt = datetime.datetime.combine(start_date, datetime.time.min)
    end_dt = datetime.datetime.combine(today, datetime.time.max)

    # Pull raw audit rows
    raw_events = _fetch_audit_events(server_id, start_dt, end_dt)

    # Pre‑process timestamps
    for ev in raw_events:
        ev["_ts"] = datetime.datetime.fromisoformat(ev["timestamp"])

    # Group by day
    series: List[Dict] = []
    total_events = 0
    for offset in range(days):
        day = start_date + datetime.timedelta(days=offset)
        day_str = day.isoformat()
        day_events = [
            ev for ev in raw_events if ev["_ts"].date() == day
        ]
        count = len(day_events)
        total_events += count
        event_types = sorted({ev["action"] for ev in day_events})
        actors = sorted({ev["actor"] for ev in day_events})
        series.append(
            {
                "date": day_str,
                "event_count": count,
                "event_types": event_types,
                "actors": actors,
            }
        )

    return {
        "server_id": server_id,
        "days": days,
        "total_events": total_events,
        "series": series,
    }


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # In‑memory audit_log store for the self‑test
    _AUDIT_LOG_STORE: List[Dict] = []

    app = FastAPI()

    @app.post("/query")
    def query_endpoint(payload: Dict) -> Dict:
        """
        Very small mock of the write‑service ``/query`` endpoint.
        It expects a ``params`` dict with ``sid``, ``start_ts`` and ``end_ts``.
        """
        params = payload.get("params", {})
        sid = params.get("sid")
        start_ts = datetime.datetime.fromisoformat(params.get("start_ts"))
        end_ts = datetime.datetime.fromisoformat(params.get("end_ts"))
        rows = [
            row
            for row in _AUDIT_LOG_STORE
            if row["server_id"] == sid
            and start_ts <= datetime.datetime.fromisoformat(row["timestamp"]) <= end_ts
        ]
        return {"rows": rows}

    client = TestClient(app)

    # Monkey‑patch ``requests.post`` so that the production code talks to the mock.
    _original_post = requests.post

    def _mock_post(url: str, json: Dict, timeout: int = 5):  # type: ignore
        if url.startswith("http://127.0.0.1:8772"):
            path = url.replace("http://127.0.0.1:8772", "")
            resp = client.post(path, json=json)
            class _Resp:
                def __init__(self, r):
                    self._r = r

                def raise_for_status(self):
                    if self._r.status_code >= 400:
                        raise Exception(f"HTTP {self._r.status_code}")

                def json(self):
                    return self._r.json()

            return _Resp(resp)
        return _original_post(url, json=json, timeout=timeout)

    requests.post = _mock_post  # type: ignore

    # Seed the mock audit_log with 5 events across 2 servers over 3 days
    now = datetime.datetime.utcnow()
    day0 = (now - datetime.timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    day1 = (now - datetime.timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    day2 = now.replace(hour=14, minute=0, second=0, microsecond=0)

    _AUDIT_LOG_STORE.extend(
        [
            {
                "server_id": 1,
                "timestamp": (day0 + datetime.timedelta(hours=1)).isoformat(),
                "target_server_id": 2,
                "action": "restart",
                "actor": "admin",
                "detail": "manual restart",
            },
            {
                "server_id": 1,
                "timestamp": (day1 + datetime.timedelta(hours=2)).isoformat(),
                "target_server_id": 2,
                "action": "update",
                "actor": "ci",
                "detail": "auto update",
            },
            {
                "server_id": 1,
                "timestamp": (day2 + datetime.timedelta(hours=3)).isoformat(),
                "target_server_id": 2,
                "action": "scan",
                "actor": "scanner",
                "detail": "security scan",
            },
            {
                "server_id": 2,
                "timestamp": (day0 + datetime.timedelta(hours=4)).isoformat(),
                "target_server_id": 1,
                "action": "restart",
                "actor": "admin",
                "detail": "other server",
            },
            {
                "server_id": 2,
                "timestamp": (day2 + datetime.timedelta(hours=5)).isoformat(),
                "target_server_id": 1,
                "action": "alert",
                "actor": "monitor",
                "detail": "high load",
            },
        ]
    )

    # Execute the timeline query for server_id=1 over the past 3 days
    result = query(server_id=1, days=3)

    # Assertions
    assert result["server_id"] == 1
    assert result["days"] == 3
    assert len(result["series"]) == 3

    # Day‑specific checks
    day0_str = (now - datetime.timedelta(days=2)).date().isoformat()
    day1_str = (now - datetime.timedelta(days=1)).date().isoformat()
    day2_str = now.date().isoformat()

    day0_series = next(item for item in result["series"] if item["date"] == day0_str)
    day1_series = next(item for item in result["series"] if item["date"] == day1_str)
    day2_series = next(item for item in result["series"] if item["date"] == day2_str)

    assert day0_series["event_count"] == 1
    assert day0_series["event_types"] == ["restart"]
    assert day1_series["event_count"] == 1
    assert day1_series["event_types"] == ["update"]
    assert day2_series["event_count"] == 1
    assert day2_series["event_types"] == ["scan"]

    print("PASS")