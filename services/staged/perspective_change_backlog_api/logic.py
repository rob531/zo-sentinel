from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent


_TIER_RANKS = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "moderate": 2,
    "low": 1,
    "info": 0,
    "informational": 0,
    "unknown": 0,
    "none": 0,
}

_CHANGE_TYPE_RANKS = {
    "critical": 5,
    "severe": 5,
    "high": 4,
    "degradation": 4,
    "degraded": 4,
    "worsened": 4,
    "increase": 3,
    "upgrade": 3,
    "changed": 2,
    "change": 2,
    "medium": 2,
    "decrease": 1,
    "downgrade": 1,
    "improvement": 1,
    "improved": 1,
    "low": 1,
    "info": 0,
}


def _as_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return dict(row)


def _is_seen(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y", "seen"}
    return bool(value)


def _serialize_time(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _normalized(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _tier_rank(value: Any) -> float:
    normalized = _normalized(value)
    if normalized in _TIER_RANKS:
        return float(_TIER_RANKS[normalized])
    if normalized in {"a", "tier_a"}:
        return 4.0
    if normalized in {"b", "tier_b"}:
        return 3.0
    if normalized in {"c", "tier_c"}:
        return 2.0
    if normalized in {"d", "tier_d"}:
        return 1.0
    try:
        return float(normalized)
    except (TypeError, ValueError):
        return 0.0


def _change_type_rank(value: Any) -> int:
    normalized = _normalized(value)
    if normalized in _CHANGE_TYPE_RANKS:
        return _CHANGE_TYPE_RANKS[normalized]
    if "critical" in normalized or "severe" in normalized:
        return 5
    if "high" in normalized or "degrad" in normalized or "worse" in normalized:
        return 4
    if "increase" in normalized or "upgrade" in normalized:
        return 3
    if "medium" in normalized:
        return 2
    if "decrease" in normalized or "downgrade" in normalized or "improve" in normalized:
        return 1
    return 0


def _event_sort_key(event: Mapping[str, Any]) -> tuple[float, float, float, int, str, str]:
    old_rank = _tier_rank(event.get("old_tier"))
    new_rank = _tier_rank(event.get("new_tier"))
    created_at = _serialize_time(event.get("created_at"))
    created_key = created_at if isinstance(created_at, str) else ""
    event_id = event.get("id")
    return (
        new_rank,
        max(old_rank, new_rank),
        abs(new_rank - old_rank),
        _change_type_rank(event.get("change_type")),
        created_key,
        str(event_id if event_id is not None else ""),
    )


def _worst_change(events: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not events:
        return None
    worst = max(events, key=_event_sort_key)
    return {
        "server_id": worst.get("server_id"),
        "old_tier": worst.get("old_tier"),
        "new_tier": worst.get("new_tier"),
        "change_type": worst.get("change_type"),
    }


def summarize_events(rows: list[dict]) -> dict:
    by_perspective: dict[Any, list[Mapping[str, Any]]] = {}
    change_type_counts: dict[str, int] = {}
    total_events = 0
    total_unseen = 0

    for raw_row in rows:
        row = raw_row if isinstance(raw_row, Mapping) else _as_dict(raw_row)
        perspective_id = row.get("perspective_id")
        by_perspective.setdefault(perspective_id, []).append(row)

        total_events += 1
        if not _is_seen(row.get("seen")):
            total_unseen += 1
            change_type = row.get("change_type")
            change_type_key = "" if change_type is None else str(change_type)
            change_type_counts[change_type_key] = change_type_counts.get(change_type_key, 0) + 1

    perspectives: list[dict[str, Any]] = []
    for perspective_id in sorted(by_perspective, key=lambda value: str(value)):
        events = by_perspective[perspective_id]
        unseen_events = [event for event in events if not _is_seen(event.get("seen"))]
        seen = len(events) - len(unseen_events)
        event_times = [
            _serialize_time(event.get("created_at"))
            for event in events
            if event.get("created_at") is not None
        ]
        last_event_at = max(event_times) if event_times else None

        perspectives.append(
            {
                "perspective_id": perspective_id,
                "total_events": len(events),
                "unseen": len(unseen_events),
                "seen": seen,
                "worst_change": _worst_change(unseen_events or events),
                "last_event_at": last_event_at,
            }
        )

    change_types = [
        {"change_type": change_type, "count": count}
        for change_type, count in sorted(
            change_type_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]

    return {
        "perspectives": perspectives,
        "totals": {
            "events": total_events,
            "unseen": total_unseen,
        },
        "change_types": change_types,
    }


def list_events(session: Session) -> list[dict]:
    statement = select(
        PerspectiveEvent.id,
        PerspectiveEvent.perspective_id,
        PerspectiveEvent.server_id,
        PerspectiveEvent.change_type,
        PerspectiveEvent.old_tier,
        PerspectiveEvent.new_tier,
        PerspectiveEvent.seen,
        PerspectiveEvent.created_at,
    ).order_by(PerspectiveEvent.created_at.asc(), PerspectiveEvent.id.asc())
    return [dict(row) for row in session.execute(statement).mappings().all()]


def get_change_backlog(session: Session = Depends(get_session)) -> dict:
    return summarize_events(list_events(session))


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE perspective_events (
                id INTEGER PRIMARY KEY,
                perspective_id INTEGER NOT NULL,
                server_id INTEGER NOT NULL,
                change_type VARCHAR NOT NULL,
                old_tier VARCHAR,
                new_tier VARCHAR,
                seen BOOLEAN,
                created_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO perspective_events
                (id, perspective_id, server_id, change_type, old_tier, new_tier, seen, created_at)
            VALUES
                (1, 1, 101, 'tier_upgrade', 'low', 'critical', 0, '2025-01-01T10:00:00'),
                (2, 1, 102, 'scope_change', 'medium', 'high', NULL, '2025-01-01T11:00:00'),
                (3, 2, 103, 'tier_downgrade', 'critical', 'medium', 1, '2025-01-01T12:00:00'),
                (4, 2, 104, 'metadata_change', 'low', 'medium', NULL, '2025-01-01T13:00:00')
            """
        )

    database_session = session_factory()
    test_app = FastAPI()

    def override_get_session():
        yield database_session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/perspectives/change-backlog")
    def change_backlog(session: Session = Depends(get_session)) -> dict:
        return get_change_backlog(session=session)

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/perspectives/change-backlog")
            assert response.status_code == 200
            payload = response.json()
            assert payload["totals"]["events"] == 4
            assert payload["totals"]["unseen"] == 3
            assert len(payload["perspectives"]) == 2
            assert any(
                item["change_type"] == "tier_upgrade" for item in payload["change_types"]
            )
    finally:
        database_session.close()
        engine.dispose()

    print("PASS")