from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.db import get_session
from app.models import McpServerRegistry


AGE_BANDS = ("never_assessed", "0-7d", "7-30d", "30-90d", "90d+")
UNKNOWN = "unknown"


def _utc_date(value: datetime) -> date:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).date()
    return value.astimezone(timezone.utc).date()


def _age_band(last_assessed: datetime | None, today: date) -> tuple[str, int | None]:
    if last_assessed is None:
        return "never_assessed", None

    age_days = max((today - _utc_date(last_assessed)).days, 0)
    if age_days <= 7:
        band = "0-7d"
    elif age_days <= 30:
        band = "7-30d"
    elif age_days <= 90:
        band = "30-90d"
    else:
        band = "90d+"
    return band, age_days


def get_verdict_ageing(
    db: Any = None,
    days: int = 30,
    *,
    session: Any = None,
) -> dict[str, Any]:
    if session is not None:
        if db is None:
            db = session
        elif isinstance(db, int) and not isinstance(days, int):
            db, days = days, db
    elif isinstance(db, int) and not isinstance(days, int):
        db, days = days, db

    if db is None:
        raise ValueError("A database session is required")

    days = int(days)
    today = datetime.now(timezone.utc).date()
    rows = db.query(
        McpServerRegistry.last_assessed,
        McpServerRegistry.verdict,
        McpServerRegistry.registry_source,
    ).all()

    by_verdict: dict[str, dict[str, int]] = {}
    by_source: dict[str, dict[str, int]] = {}
    never_assessed = 0
    stale_total = 0

    for row in rows:
        band, age_days = _age_band(row.last_assessed, today)
        if band == "never_assessed":
            never_assessed += 1

        if age_days is None or age_days > days:
            stale_total += 1

        verdict = row.verdict if row.verdict is not None else UNKNOWN
        source = row.registry_source if row.registry_source is not None else UNKNOWN

        verdict_counts = by_verdict.setdefault(
            verdict, {candidate: 0 for candidate in AGE_BANDS}
        )
        source_counts = by_source.setdefault(
            source, {candidate: 0 for candidate in AGE_BANDS}
        )
        verdict_counts[band] += 1
        source_counts[band] += 1

    return {
        "days": days,
        "total_servers": len(rows),
        "never_assessed": never_assessed,
        "by_verdict": by_verdict,
        "by_source": by_source,
        "stale_total": stale_total,
    }


get_verdict_ageing_data = get_verdict_ageing
get_verdict_ageing_breakdown = get_verdict_ageing
get_registry_verdict_ageing = get_verdict_ageing
compute_verdict_ageing = get_verdict_ageing
get_ageing_summary = get_verdict_ageing
get_ageing_breakdown = get_verdict_ageing


def _self_test() -> None:
    import asyncio
    import json
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    seeds = [
        ("null-1", "Null One", "alpha", "safe", None),
        ("null-2", "Null Two", "alpha", "safe", None),
        ("age-1", "Age One", "alpha", "safe", now - timedelta(days=1)),
        ("age-5", "Age Five", "beta", "safe", now - timedelta(days=5)),
        ("age-10", "Age Ten", "beta", "safe", now - timedelta(days=10)),
        ("age-45", "Age Forty Five", "gamma", "caution", now - timedelta(days=45)),
        ("age-120", "Age One Hundred Twenty", "gamma", "safe", now - timedelta(days=120)),
    ]

    with session_factory() as session:
        for server_id, name, source, verdict, last_assessed in seeds:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=name,
                    registry_source=source,
                    verdict=verdict,
                    last_assessed=last_assessed,
                )
            )
        session.commit()

    that_app = FastAPI()

    @that_app.get("/api/registry/verdict-ageing")
    def verdict_ageing_endpoint(days: int = 30, db: Any = Depends(get_session)):
        return get_verdict_ageing(db=db, days=days)

    def override_session():
        with session_factory() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_session

    async def request() -> tuple[int, bytes]:
        response_messages: list[dict[str, Any]] = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            response_messages.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/registry/verdict-ageing",
            "raw_path": b"/api/registry/verdict-ageing",
            "query_string": b"days=30",
            "root_path": "",
            "headers": [(b"host", b"testserver"), (b"accept", b"application/json")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        await that_app(scope, receive, send)
        status = next(
            message["status"]
            for message in response_messages
            if message["type"] == "http.response.start"
        )
        body = b"".join(
            message.get("body", b"")
            for message in response_messages
            if message["type"] == "http.response.body"
        )
        return status, body

    status, body = asyncio.run(request())
    payload = json.loads(body)

    assert status == 200
    assert payload["days"] == 30
    assert payload["total_servers"] == len(seeds)
    assert payload["never_assessed"] == 2
    assert payload["by_verdict"]["safe"]["0-7d"] == 2
    assert payload["by_source"]["alpha"]["0-7d"] == 1
    assert payload["stale_total"] == 4

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()