"""Lifecycle churn classification over the real MCP server registry."""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _iso(value: Any) -> Optional[str]:
    result = _as_datetime(value)
    return result.isoformat() if result is not None else None


def _session_context():
    context = get_session()
    if hasattr(context, "__enter__"):
        return context, None
    return None, context


def registry_lifecycle_churn(days: int = 30, db: Optional[Session] = None) -> Dict[str, Any]:
    """Return registry freshness and inactivity buckets.

    The optional session keeps the function convenient for the router while
    preserving the public ``registry_lifecycle_churn(days)`` interface.
    """
    if isinstance(days, Session) and db is None:
        db = days
        days = 30

    owned_context = None
    owned_generator = None
    if db is None:
        owned_context, owned_generator = _session_context()
        if owned_context is not None:
            db = owned_context.__enter__()
        else:
            db = next(owned_generator)

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(days))
        rows = db.execute(
            select(
                McpServerRegistry.first_seen,
                McpServerRegistry.last_seen,
                McpServerRegistry.last_scanned,
                McpServerRegistry.scan_count,
            )
        ).all()

        buckets = {
            "new": 0,
            "dormant": 0,
            "stale_active": 0,
            "churned": 0,
            "active": 0,
        }
        first_seen_values = []

        for row in rows:
            first_seen = _as_datetime(row.first_seen)
            last_seen = _as_datetime(row.last_seen)
            last_scanned = _as_datetime(row.last_scanned)
            scan_count = int(row.scan_count or 0)

            if first_seen is not None:
                first_seen_values.append(first_seen)

            first_seen_recent = first_seen is not None and first_seen >= cutoff
            last_seen_recent = last_seen is not None and last_seen >= cutoff
            last_scanned_recent = last_scanned is not None and last_scanned >= cutoff

            if first_seen_recent:
                bucket = "new"
            elif not last_seen_recent:
                bucket = "dormant"
            elif not last_scanned_recent:
                bucket = "stale_active"
            elif scan_count >= 3 and first_seen is not None and first_seen < cutoff:
                bucket = "churned"
            else:
                bucket = "active"

            buckets[bucket] += 1

        oldest = min(first_seen_values).isoformat() if first_seen_values else None
        newest = max(first_seen_values).isoformat() if first_seen_values else None

        return {
            "days": int(days),
            "buckets": buckets,
            "total_servers": len(rows),
            "oldest_first_seen": oldest,
            "newest_first_seen": newest,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        if owned_context is not None:
            owned_context.__exit__(None, None, None)
        elif owned_generator is not None:
            close = getattr(owned_generator, "close", None)
            if close is not None:
                close()


if __name__ == "__main__":
    import asyncio
    import json

    from fastapi import Depends, FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    test_session = TestSession()
    test_session.add_all(
        [
            McpServerRegistry(
                server_id="new",
                first_seen=now - timedelta(days=2),
                last_seen=now,
                last_scanned=now,
                scan_count=1,
            ),
            McpServerRegistry(
                server_id="dormant",
                first_seen=now - timedelta(days=100),
                last_seen=now - timedelta(days=100),
                last_scanned=now - timedelta(days=100),
                scan_count=1,
            ),
            McpServerRegistry(
                server_id="stale-active",
                first_seen=now - timedelta(days=100),
                last_seen=now,
                last_scanned=now - timedelta(days=100),
                scan_count=1,
            ),
            McpServerRegistry(
                server_id="churned",
                first_seen=now - timedelta(days=100),
                last_seen=now,
                last_scanned=now,
                scan_count=3,
            ),
            McpServerRegistry(
                server_id="active",
                first_seen=now - timedelta(days=100),
                last_seen=now,
                last_scanned=now,
                scan_count=1,
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()

    @test_app.get("/api/registry/lifecycle-churn")
    def lifecycle_churn(days: int = 30, db: Session = Depends(get_session)):
        return registry_lifecycle_churn(days=days, db=db)

    test_app.dependency_overrides[get_session] = lambda: test_session

    async def invoke_http() -> tuple[int, bytes]:
        status = 0
        body = bytearray()

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                body.extend(message.get("body", b""))

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/registry/lifecycle-churn",
            "raw_path": b"/api/registry/lifecycle-churn",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
        }
        await test_app(scope, receive, send)
        return status, bytes(body)

    http_status, http_body = asyncio.run(invoke_http())
    assert http_status == 200, (http_status, http_body)
    result = json.loads(http_body)
    assert all(result["buckets"][name] == 1 for name in (
        "new", "dormant", "stale_active", "churned", "active"
    )), result
    assert result["total_servers"] == 5, result
    print("PASS")
    test_session.close()
    test_engine.dispose()