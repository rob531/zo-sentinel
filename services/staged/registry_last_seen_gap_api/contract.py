from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


def get_last_seen_gap(
    hours: int = Query(..., gt=0),
    session: Session = Depends(get_session),
) -> Dict[str, object]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows: List[McpServerRegistry] = list(
        session.execute(
            select(McpServerRegistry)
            .where(McpServerRegistry.last_seen < cutoff)
            .order_by(McpServerRegistry.last_seen.asc(), McpServerRegistry.server_id.asc())
        )
        .scalars()
        .all()
    )

    servers = [
        {
            "server_id": row.server_id,
            "name": row.name,
            "registry_source": row.registry_source,
            "last_seen": row.last_seen.isoformat() if row.last_seen is not None else None,
            "scan_count": row.scan_count,
            "verdict": row.verdict,
            "risk_tier": row.risk_tier,
        }
        for row in rows
    ]

    return {
        "hours": hours,
        "stale_count": len(servers),
        "by_source": dict(Counter(row["registry_source"] for row in servers)),
        "servers": servers,
    }


router.get("/registry/last-seen-gap")(get_last_seen_gap)

app = FastAPI()
app.include_router(router)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    records = [
        {
            "server_id": "stale-oldest",
            "name": "Oldest stale registry entry",
            "registry_source": "github",
            "first_seen": now - timedelta(hours=72),
            "last_seen": now - timedelta(hours=48),
            "last_scanned": now - timedelta(hours=48),
            "scan_count": 3,
            "verdict": "malicious",
            "risk_tier": "high",
        },
        {
            "server_id": "stale-recent",
            "name": "Recently stale registry entry",
            "registry_source": "npm",
            "first_seen": now - timedelta(hours=30),
            "last_seen": now - timedelta(hours=30),
            "last_scanned": now - timedelta(hours=30),
            "scan_count": 5,
            "verdict": "suspicious",
            "risk_tier": "medium",
        },
        {
            "server_id": "fresh",
            "name": "Fresh registry entry",
            "registry_source": "github",
            "first_seen": now - timedelta(hours=2),
            "last_seen": now - timedelta(hours=2),
            "last_scanned": now - timedelta(hours=2),
            "scan_count": 8,
            "verdict": "benign",
            "risk_tier": "low",
        },
    ]

    with test_session_factory() as session:
        session.execute(McpServerRegistry.__table__.insert(), records)
        session.commit()

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/registry/last-seen-gap", params={"hours": 24})

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["hours"] == 24
        assert payload["stale_count"] == 2
        assert payload["by_source"] == {"github": 1, "npm": 1}
        assert [server["server_id"] for server in payload["servers"]] == [
            "stale-oldest",
            "stale-recent",
        ]
        assert all(server["server_id"] != "fresh" for server in payload["servers"])
    finally:
        engine.dispose()


if __name__ == "__main__":
    _run_self_test()
    print("PASS")