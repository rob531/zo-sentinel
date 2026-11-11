from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


__all__ = [
    "get_registry_tier_aging",
    "get_registry_tier_aging_summary",
    "get_tier_aging",
]


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _isoformat(value: Any) -> str | None:
    normalized = _as_datetime(value)
    return normalized.isoformat() if normalized is not None else None


def _staleness_band(last_assessed: Any, now: datetime) -> str | None:
    assessed = _as_datetime(last_assessed)
    if assessed is None:
        return None

    age = now - assessed
    if age <= timezone.utc.utcoffset(now) * 0 + __import__("datetime").timedelta(days=7):
        return "fresh"
    if age <= __import__("datetime").timedelta(days=30):
        return "aging"
    return "stale"


def _calculate_registry_tier_aging(db: Session) -> dict[str, Any]:
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.verdict,
        McpServerRegistry.risk_tier,
        McpServerRegistry.last_assessed,
        McpServerRegistry.last_seen,
        McpServerRegistry.first_seen,
        McpServerRegistry.scan_count,
    )
    rows = db.execute(statement).mappings().all()

    now = datetime.now(timezone.utc)
    grouped: dict[Any, dict[str, Any]] = {}

    for row in rows:
        tier = row["risk_tier"]
        group = grouped.setdefault(
            tier,
            {
                "tier": tier,
                "total": 0,
                "fresh": 0,
                "aging": 0,
                "stale": 0,
                "oldest_assessed_at": None,
                "_oldest": None,
            },
        )
        group["total"] += 1

        band = _staleness_band(row["last_assessed"], now)
        if band is not None:
            group[band] += 1
            assessed = _as_datetime(row["last_assessed"])
            if assessed is not None and (
                group["_oldest"] is None or assessed < group["_oldest"]
            ):
                group["_oldest"] = assessed
                group["oldest_assessed_at"] = assessed.isoformat()

    tiers = []
    for group in sorted(
        grouped.values(),
        key=lambda item: (item["tier"] is None, str(item["tier"])),
    ):
        group.pop("_oldest", None)
        tiers.append(group)

    return {
        "generated_at": now.isoformat(),
        "total": len(rows),
        "tiers": tiers,
    }


def get_registry_tier_aging(db: Session = Depends(get_session)) -> dict[str, Any]:
    return _calculate_registry_tier_aging(db)


def get_registry_tier_aging_summary(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return _calculate_registry_tier_aging(db)


def get_tier_aging(db: Session = Depends(get_session)) -> dict[str, Any]:
    return _calculate_registry_tier_aging(db)


async def _run_self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(engine) as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="fresh-server",
                    name="Fresh Server",
                    verdict="approved",
                    risk_tier="high",
                    last_assessed=now - __import__("datetime").timedelta(days=2),
                    last_seen=now,
                    first_seen=now - __import__("datetime").timedelta(days=100),
                    scan_count=3,
                ),
                McpServerRegistry(
                    server_id="aging-server",
                    name="Aging Server",
                    verdict="conditional",
                    risk_tier="high",
                    last_assessed=now - __import__("datetime").timedelta(days=10),
                    last_seen=now,
                    first_seen=now - __import__("datetime").timedelta(days=100),
                    scan_count=2,
                ),
                McpServerRegistry(
                    server_id="stale-server",
                    name="Stale Server",
                    verdict="rejected",
                    risk_tier="medium",
                    last_assessed=now - __import__("datetime").timedelta(days=40),
                    last_seen=now,
                    first_seen=now - __import__("datetime").timedelta(days=100),
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="unassessed-server",
                    name="Unassessed Server",
                    verdict="unknown",
                    risk_tier="medium",
                    last_assessed=None,
                    last_seen=now,
                    first_seen=now - __import__("datetime").timedelta(days=100),
                    scan_count=0,
                ),
            ]
        )
        session.commit()

        def override_get_session():
            yield session

        that_app = FastAPI()
        that_app.dependency_overrides[get_session] = override_get_session

        @that_app.get("/api/registry/tier-aging")
        async def tier_aging_endpoint(db: Session = Depends(get_session)):
            return get_registry_tier_aging(db)

        status_code = None
        response_body = b""

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            nonlocal status_code, response_body
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body":
                response_body += message.get("body", b"")

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/registry/tier-aging",
            "raw_path": b"/api/registry/tier-aging",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }

        await that_app(scope, receive, send)

        assert status_code == 200
        payload = json.loads(response_body)
        assert payload["total"] == 4
        assert len(payload["tiers"]) == 2

        by_tier = {entry["tier"]: entry for entry in payload["tiers"]}
        assert by_tier["high"]["fresh"] == 1
        assert by_tier["high"]["aging"] == 1
        assert by_tier["high"]["stale"] == 0
        assert by_tier["medium"]["fresh"] == 0
        assert by_tier["medium"]["aging"] == 0
        assert by_tier["medium"]["stale"] == 1

    engine.dispose()


if __name__ == "__main__":
    asyncio.run(_run_self_test())
    print("PASS")