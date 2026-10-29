from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter()


_LABEL_INDEX = {
    "none": 0,
    "no": 0,
    "info": 0,
    "informational": 0,
    "safe": 0,
    "green": 1,
    "low": 1,
    "minor": 1,
    "a": 1,
    "amber": 2,
    "medium": 2,
    "moderate": 2,
    "b": 2,
    "orange": 3,
    "high": 3,
    "major": 3,
    "c": 3,
    "red": 4,
    "critical": 4,
    "severe": 4,
    "d": 4,
    "black": 5,
    "block": 5,
    "e": 5,
}

_LEVEL_PATTERN = re.compile(
    r"^(?:level|tier|label|score)?[\s_-]*([0-9]+)$", re.IGNORECASE
)


def _label_index(label: Any) -> int | None:
    if label is None:
        return None

    value = str(label).strip().lower()
    if not value:
        return None

    if value in _LABEL_INDEX:
        return _LABEL_INDEX[value]

    match = _LEVEL_PATTERN.match(value)
    if match:
        return int(match.group(1))

    if len(value) == 1 and value.isalpha():
        return ord(value) - ord("a") + 1

    return None


def _event_date(value: Any) -> date | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).date()
        return value.date()

    if isinstance(value, date):
        return value

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).date()

    text_value = str(value).strip()
    if not text_value:
        return None

    try:
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).date()
        return parsed.date()
    except ValueError:
        try:
            return date.fromisoformat(text_value[:10])
        except ValueError:
            return None


def get_score_change_rollup(
    days: int = 30,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if days < 1:
        raise HTTPException(status_code=422, detail="days must be at least 1")

    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=days - 1)

    # Read the authoritative registry through the real application model.
    registry_rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
    ).all()
    registry_servers = {row.server_id for row in registry_rows}

    # The event ledger is read with portable SQL; date grouping is performed
    # after parsing so the same implementation works on Postgres and SQLite.
    event_rows = session.execute(
        text(
            """
            SELECT
                e.server_id,
                e.axis_name,
                e.old_label,
                e.new_label,
                e.changed_at
            FROM mcp_score_change_events AS e
            JOIN mcp_server_registry AS r
              ON r.server_id = e.server_id
            WHERE e.changed_at IS NOT NULL
            """
        )
    ).mappings().all()

    day_stats: dict[date, dict[str, Any]] = {
        start_date + timedelta(days=offset): {
            "change_count": 0,
            "escalations": 0,
            "de_escalations": 0,
            "servers": set(),
        }
        for offset in range(days)
    }
    axis_counts: Counter[str] = Counter()

    for event in event_rows:
        server_id = event["server_id"]
        if registry_servers and server_id not in registry_servers:
            continue

        changed_on = _event_date(event["changed_at"])
        if changed_on is None or changed_on not in day_stats:
            continue

        stats = day_stats[changed_on]
        stats["change_count"] += 1
        stats["servers"].add(server_id)

        axis_name = str(event["axis_name"] or "")
        axis_counts[axis_name] += 1

        old_index = _label_index(event["old_label"])
        new_index = _label_index(event["new_label"])
        if old_index is None or new_index is None:
            continue

        if new_index > old_index:
            stats["escalations"] += 1
        elif new_index < old_index:
            stats["de_escalations"] += 1

    series = [
        {
            "date": current_date.isoformat(),
            "change_count": stats["change_count"],
            "escalations": stats["escalations"],
            "de_escalations": stats["de_escalations"],
            "servers_touched": len(stats["servers"]),
        }
        for current_date, stats in sorted(day_stats.items())
    ]

    top_axes = [
        {"axis_name": axis_name, "change_count": count}
        for axis_name, count in sorted(
            axis_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]

    return {
        "days": days,
        "series": series,
        "top_axes": top_axes,
    }


@router.get("/api/score-changes/rollup")
def score_change_rollup(
    days: int = 30,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_score_change_rollup(days=days, session=session)


get_rollup = get_score_change_rollup


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()

    @test_app.get("/api/score-changes/rollup")
    def test_endpoint(
        days: int = 30,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_score_change_rollup(days=days, session=session)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine)
    test_session = TestingSession()

    test_session.execute(
        text(
            """
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                risk_tier TEXT
            )
            """
        )
    )
    test_session.execute(
        text(
            """
            CREATE TABLE mcp_score_change_events (
                server_id TEXT,
                axis_name TEXT,
                old_label TEXT,
                new_label TEXT,
                changed_at TIMESTAMP
            )
            """
        )
    )
    test_session.execute(
        text(
            """
            INSERT INTO mcp_server_registry (server_id, name, risk_tier)
            VALUES (:server_id, :name, :risk_tier)
            """
        ),
        {"server_id": "server-1", "name": "Test Server", "risk_tier": "medium"},
    )

    now = datetime.now(timezone.utc)
    test_session.execute(
        text(
            """
            INSERT INTO mcp_score_change_events
                (server_id, axis_name, old_label, new_label, changed_at)
            VALUES
                (:server_id, :axis_name, :old_label, :new_label, :changed_at)
            """
        ),
        [
            {
                "server_id": "server-1",
                "axis_name": "known_axis",
                "old_label": "low",
                "new_label": "high",
                "changed_at": now - timedelta(days=1),
            },
            {
                "server_id": "server-1",
                "axis_name": "known_axis",
                "old_label": "high",
                "new_label": "low",
                "changed_at": now,
            },
            {
                "server_id": "server-1",
                "axis_name": "known_axis",
                "old_label": "medium",
                "new_label": "medium",
                "changed_at": now,
            },
        ],
    )
    test_session.commit()

    test_app.dependency_overrides[get_session] = lambda: test_session
    response = TestClient(test_app).get(
        "/api/score-changes/rollup", params={"days": 30}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["days"] == 30
    assert len(payload["series"]) >= 2

    by_date = {item["date"]: item for item in payload["series"]}
    current = by_date[now.date().isoformat()]
    assert current["change_count"] == 2
    assert current["escalations"] == 0
    assert current["de_escalations"] == 1
    assert payload["top_axes"]
    assert payload["top_axes"][0]["axis_name"] == "known_axis"

    print("PASS")