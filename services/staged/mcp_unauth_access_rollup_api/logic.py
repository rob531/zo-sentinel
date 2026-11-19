"""Read-only rollup logic for unauthenticated MCP access exposure."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpUnauthAccess


router = APIRouter(prefix="/api", tags=["unauth-access"])


def _string_value(value: Any) -> str:
    """Convert nullable database values to the API's string representation."""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _datetime_value(value: Any) -> Optional[datetime]:
    """Normalize supported timestamp values for age comparisons."""
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
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


def _group_count(rows: List[McpUnauthAccess], attribute: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = getattr(row, attribute)
        key = value if value is not None else "unknown"
        counts[str(key)] = counts.get(str(key), 0) + 1
    return counts


def build_unauth_access_rollup(
    db: Session,
    source: Optional[str] = None,
    min_age_days: int = 0,
) -> Dict[str, Any]:
    """Build the unauthenticated-access rollup from real application rows."""
    statement = select(McpUnauthAccess)
    if source is not None:
        statement = statement.where(McpUnauthAccess.source == source)

    rows = list(db.execute(statement).scalars().all())
    by_access = _group_count(rows, "unauth_access")
    by_source = _group_count(rows, "source")

    try:
        age_days = max(0, int(min_age_days))
    except (TypeError, ValueError):
        age_days = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=age_days)

    stale_count = 0
    for row in rows:
        updated_at = _datetime_value(row.updated_at)
        if updated_at is not None and updated_at < cutoff:
            stale_count += 1

    def recency(row: McpUnauthAccess) -> str:
        updated_at = _datetime_value(row.updated_at)
        return updated_at.isoformat() if updated_at is not None else ""

    top_rows = sorted(rows, key=recency, reverse=True)[:10]
    top_servers = [
        {
            "server_id": _string_value(row.server_id),
            "url": _string_value(row.url),
            "unauth_access": _string_value(row.unauth_access),
            "source": _string_value(row.source),
            "updated_at": _string_value(row.updated_at),
        }
        for row in top_rows
    ]

    return {
        "total_servers": len(rows),
        "by_access": by_access,
        "by_source": by_source,
        "stale_count": stale_count,
        "top_servers": top_servers,
    }


@router.get("/unauth-access/rollup")
def unauth_access_rollup(
    source: Optional[str] = None,
    min_age_days: int = 0,
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Return a read-only rollup of mcp_unauth_access records."""
    return build_unauth_access_rollup(
        db=db,
        source=source,
        min_age_days=min_age_days,
    )


def _self_test() -> None:
    """Exercise the route against a temporary local store."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpUnauthAccess.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    seeded = [
        ("server-1", "https://one.example", "allowed", "registry", now),
        ("server-2", "https://two.example", "allowed", "registry", now),
        ("server-3", "https://three.example", "blocked", "scanner", now),
        ("server-4", "https://four.example", "blocked", "scanner", now),
        ("server-5", "https://five.example", "unknown", "registry", now),
        (
            "server-6",
            "https://six.example",
            "unknown",
            "scanner",
            now - timedelta(days=90),
        ),
    ]

    with TestingSession() as session:
        for server_id, url, access, row_source, updated_at in seeded:
            session.add(
                McpUnauthAccess(
                    server_id=server_id,
                    url=url,
                    unauth_access=access,
                    source=row_source,
                    updated_at=updated_at,
                )
            )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_session
    client = TestClient(test_app)

    response = client.get("/api/unauth-access/rollup", params={"min_age_days": 30})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_servers"] == 6
    assert payload["by_access"] == {"allowed": 2, "blocked": 2, "unknown": 2}
    assert payload["by_source"] == {"registry": 3, "scanner": 3}
    assert payload["stale_count"] == 1
    assert payload["top_servers"]
    assert all(
        {
            "server_id",
            "url",
            "unauth_access",
            "source",
            "updated_at",
        }.issubset(entry)
        for entry in payload["top_servers"]
    )

    filtered = client.get(
        "/api/unauth-access/rollup",
        params={"source": "registry", "min_age_days": 30},
    )
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert filtered_payload["total_servers"] == 3
    assert filtered_payload["by_source"] == {"registry": 3}

    print("PASS")


if __name__ == "__main__":
    _self_test()