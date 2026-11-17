"""Logic for the freshness SLA breach service."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import FreshnessStat, McpServerRegistry


router = APIRouter(prefix="/api")

FIRST_VERDICT_SLA_HOURS = 24
RE_VERDICT_SLA_HOURS = 168


def _as_utc(value: Any) -> Optional[datetime]:
    """Convert an ORM timestamp or ISO timestamp to an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        result = datetime.fromisoformat(text)
    else:
        return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _iso(value: Any) -> Optional[str]:
    result = _as_utc(value)
    return result.isoformat() if result is not None else None


def _hours_since(value: Any, now: datetime) -> Optional[float]:
    result = _as_utc(value)
    if result is None:
        return None
    return max(0.0, (now - result).total_seconds() / 3600.0)


def get_freshness_slo(
    db: Session,
    limit: int = 50,
    only_breached: bool = False,
) -> Dict[str, Any]:
    """Return the current freshness SLA breach list and freshness snapshots."""
    limit = max(1, min(500, int(limit)))
    now = datetime.now(timezone.utc)

    servers = list(
        db.execute(
            select(McpServerRegistry).order_by(McpServerRegistry.server_id)
        ).scalars()
    )

    breached: List[Dict[str, Any]] = []
    for server in servers:
        first_seen = server.first_seen
        last_assessed = server.last_assessed
        hours_since_first_seen = _hours_since(first_seen, now)
        hours_since_assessed = _hours_since(last_assessed, now)

        breach_kind: Optional[str] = None
        if (
            last_assessed is None
            and hours_since_first_seen is not None
            and hours_since_first_seen > FIRST_VERDICT_SLA_HOURS
        ):
            breach_kind = "first_verdict"
        elif (
            last_assessed is not None
            and hours_since_assessed is not None
            and hours_since_assessed > RE_VERDICT_SLA_HOURS
        ):
            breach_kind = "re_verdict"

        if breach_kind is not None:
            breached.append(
                {
                    "server_id": server.server_id,
                    "name": server.name,
                    "first_seen": _iso(first_seen),
                    "last_assessed": _iso(last_assessed),
                    "hours_since_first_seen": hours_since_first_seen,
                    "hours_since_assessed": hours_since_assessed,
                    "breach_kind": breach_kind,
                }
            )

    stats = list(
        db.execute(
            select(FreshnessStat.surface, FreshnessStat.computed_at).order_by(
                FreshnessStat.surface
            )
        ).all()
    )

    return {
        "generated_at": now.isoformat(),
        "sla_hours": {
            "first_verdict": FIRST_VERDICT_SLA_HOURS,
            "re_verdict": RE_VERDICT_SLA_HOURS,
        },
        "total_servers": len(servers),
        "breached_count": len(breached),
        "breached": breached[:limit],
        "within_sla_count": len(servers) - len(breached),
        "freshness_stats": [
            {
                "surface": surface,
                "computed_at": _iso(computed_at),
            }
            for surface, computed_at in stats
        ],
    }


@router.get("/ops/freshness-slo")
def freshness_slo(
    limit: int = Query(50),
    only_breached: bool = Query(False),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Expose servers that are outside the product freshness SLAs."""
    return get_freshness_slo(db, limit=limit, only_breached=only_breached)


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    FreshnessStat.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="first-breach",
                    name="First breach",
                    first_seen=now.replace(tzinfo=None) - __import__("datetime").timedelta(hours=100),
                ),
                McpServerRegistry(
                    server_id="healthy",
                    name="Healthy",
                    first_seen=now.replace(tzinfo=None) - __import__("datetime").timedelta(hours=10),
                    last_assessed=now.replace(tzinfo=None) - __import__("datetime").timedelta(hours=2),
                ),
                McpServerRegistry(
                    server_id="unknown-age",
                    name="Unknown age",
                    first_seen=None,
                ),
            ]
        )
        session.add(
            FreshnessStat(
                surface="default",
                payload={"snapshot": "available"},
            )
        )
        session.commit()

    client = TestClient(that_app)
    response = client.get("/api/ops/freshness-slo")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_servers"] == 3
    assert payload["breached_count"] == 1
    assert "first_verdict" in payload["breached"][0]["breach_kind"]

    limited = client.get("/api/ops/freshness-slo?limit=1")
    assert limited.status_code == 200
    assert len(limited.json()["breached"]) <= 1

    print("PASS")