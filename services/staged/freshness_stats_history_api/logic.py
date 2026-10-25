from __future__ import annotations

import json
from typing import Any

from fastapi import Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat

_FRESHNESS_STATS_SQL = text(
    "SELECT surface, payload, computed_at, compute_seconds "
    "FROM freshness_stats "
    "WHERE surface = :surface "
    "ORDER BY computed_at DESC "
    "LIMIT :n"
)


def _decode_payload(payload: Any) -> Any:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        return json.loads(payload)
    return payload


def get_freshness_stats_history(
    surface: str,
    limit: int = 20,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    n = max(1, min(int(limit), 100))
    rows = db.execute(
        _FRESHNESS_STATS_SQL,
        {"surface": surface, "n": n},
    ).mappings().all()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No freshness stats found for surface '{surface}'")

    history = [
        {
            "computed_at": row["computed_at"],
            "compute_seconds": float(row["compute_seconds"]),
        }
        for row in rows
    ]
    latest = rows[0]

    return {
        "surface": surface,
        "count": len(history),
        "latest": {
            "computed_at": latest["computed_at"],
            "compute_seconds": float(latest["compute_seconds"]),
            "payload": _decode_payload(latest["payload"]),
        },
        "history": history,
    }


get_freshness_history = get_freshness_stats_history
get_stats_history = get_freshness_stats_history
get_history = get_freshness_stats_history


def _run_self_test() -> None:
    class _Result:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self._rows = rows

        def mappings(self) -> _Result:
            return self

        def all(self) -> list[dict[str, Any]]:
            return self._rows

    class _Session:
        def __init__(self) -> None:
            self.params: dict[str, Any] | None = None

        def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
            assert "FROM freshness_stats" in str(statement)
            assert "ORDER BY computed_at DESC" in str(statement)
            assert "LIMIT :n" in str(statement)
            self.params = params
            return _Result(
                [
                    {
                        "surface": "registry_scan",
                        "payload": {"nested": {"count": 2, "items": [1, 2]}},
                        "computed_at": "2025-01-03T00:00:00",
                        "compute_seconds": 3,
                    },
                    {
                        "surface": "registry_scan",
                        "payload": {"nested": {"count": 1}},
                        "computed_at": "2025-01-02T00:00:00",
                        "compute_seconds": 2.5,
                    },
                    {
                        "surface": "registry_scan",
                        "payload": {"nested": {"count": 0}},
                        "computed_at": "2025-01-01T00:00:00",
                        "compute_seconds": 1,
                    },
                ]
            )

    session = _Session()
    result = get_freshness_stats_history("registry_scan", 20, session)

    assert session.params == {"surface": "registry_scan", "n": 20}
    assert result["surface"] == "registry_scan"
    assert result["count"] == 3
    assert result["latest"]["computed_at"] == "2025-01-03T00:00:00"
    assert isinstance(result["latest"]["compute_seconds"], float)
    assert result["latest"]["compute_seconds"] == 3.0
    assert result["latest"]["payload"] == {
        "nested": {"count": 2, "items": [1, 2]}
    }
    assert result["history"][0]["computed_at"] == "2025-01-03T00:00:00"


if __name__ == "__main__":
    _run_self_test()
    print("PASS")