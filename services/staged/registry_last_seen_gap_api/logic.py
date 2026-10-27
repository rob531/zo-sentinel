from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


@router.get("/registry/last-seen-gap")
def get_last_seen_gap(
    hours: int = Query(..., ge=0),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    statement = (
        select(McpServerRegistry)
        .where(
            McpServerRegistry.last_seen.is_not(None),
            McpServerRegistry.last_seen < cutoff,
        )
        .order_by(McpServerRegistry.last_seen.asc())
    )

    records = db.execute(statement).scalars().all()
    by_source: Dict[str, int] = {}
    servers: List[Dict[str, Any]] = []

    for record in records:
        source = record.registry_source or "unknown"
        by_source[source] = by_source.get(source, 0) + 1
        servers.append(
            {
                "server_id": record.server_id,
                "name": record.name,
                "registry_source": record.registry_source,
                "last_seen": record.last_seen,
                "scan_count": record.scan_count,
                "verdict": record.verdict,
                "risk_tier": record.risk_tier,
            }
        )

    return {
        "hours": hours,
        "stale_count": len(servers),
        "by_source": by_source,
        "servers": servers,
    }


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    seeded = [
        {
            "server_id": "stale-five-hours",
            "name": "Stale Five Hours",
            "registry_source": "source-a",
            "first_seen": now - timedelta(days=2),
            "last_seen": now - timedelta(hours=5),
            "last_scanned": now - timedelta(hours=5),
            "scan_count": 5,
            "verdict": "malicious",
            "risk_tier": "high",
        },
        {
            "server_id": "stale-two-hours",
            "name": "Stale Two Hours",
            "registry_source": "source-b",
            "first_seen": now - timedelta(days=1),
            "last_seen": now - timedelta(hours=2),
            "last_scanned": now - timedelta(hours=2),
            "scan_count": 2,
            "verdict": "suspicious",
            "risk_tier": "medium",
        },
        {
            "server_id": "fresh-server",
            "name": "Fresh Server",
            "registry_source": "source-c",
            "first_seen": now - timedelta(hours=2),
            "last_seen": now - timedelta(minutes=30),
            "last_scanned": now - timedelta(minutes=30),
            "scan_count": 1,
            "verdict": "trusted",
            "risk_tier": "low",
        },
    ]

    with TestingSession() as session:
        session.add_all(McpServerRegistry(**record) for record in seeded)
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/last-seen-gap", params={"hours": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["stale_count"] == 2
    assert payload["servers"][0]["server_id"] == "stale-five-hours"
    assert payload["servers"][1]["server_id"] == "stale-two-hours"
    assert all(server["server_id"] != "fresh-server" for server in payload["servers"])
    assert payload["by_source"] == {"source-a": 1, "source-b": 1}

    print("PASS")


if __name__ == "__main__":
    _run_self_test()