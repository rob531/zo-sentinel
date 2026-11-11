from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class RegistryScanBacklogItem(BaseModel):
    server_id: str
    name: str
    registry_source: Optional[str]
    last_seen: Optional[datetime]
    last_scanned: Optional[datetime]
    scan_count: Optional[int]
    risk_tier: Optional[str]


class RegistryScanBacklogResponse(BaseModel):
    registry_source: Optional[str]
    count: int
    oldest_last_seen: Optional[datetime]
    backlog: List[RegistryScanBacklogItem]


def get_registry_scan_backlog(
    session: Session,
    registry_source: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    if limit <= 0:
        return []

    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.registry_source,
        McpServerRegistry.last_seen,
        McpServerRegistry.last_scanned,
        McpServerRegistry.scan_count,
        McpServerRegistry.risk_tier,
    ).where(
        or_(
            McpServerRegistry.last_scanned.is_(None),
            McpServerRegistry.last_scanned < McpServerRegistry.last_seen,
        )
    )

    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    statement = (
        statement.order_by(McpServerRegistry.last_seen.asc().nulls_last())
        .limit(limit)
    )

    return [dict(row) for row in session.execute(statement).mappings().all()]


def get_scan_backlog(
    session: Session,
    registry_source: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    return get_registry_scan_backlog(session, registry_source, limit)


def build_registry_scan_backlog_response(
    registry_source: Optional[str],
    backlog: List[Dict[str, Any]],
) -> RegistryScanBacklogResponse:
    seen_dates = [
        item["last_seen"]
        for item in backlog
        if item.get("last_seen") is not None
    ]
    oldest_last_seen = min(seen_dates) if seen_dates else None
    return RegistryScanBacklogResponse(
        registry_source=registry_source,
        count=len(backlog),
        oldest_last_seen=oldest_last_seen,
        backlog=[RegistryScanBacklogItem(**item) for item in backlog],
    )


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="never-scanned",
                    name="Never scanned",
                    registry_source="seed",
                    last_seen=datetime(2024, 1, 1, 0, 0, 0),
                    last_scanned=None,
                    scan_count=0,
                    risk_tier="unknown",
                ),
                McpServerRegistry(
                    server_id="stale",
                    name="Stale",
                    registry_source="seed",
                    last_seen=datetime(2024, 1, 2, 0, 0, 0),
                    last_scanned=datetime(2024, 1, 1, 0, 0, 0),
                    scan_count=1,
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="current",
                    name="Current",
                    registry_source="seed",
                    last_seen=datetime(2024, 1, 3, 0, 0, 0),
                    last_scanned=datetime(2024, 1, 4, 0, 0, 0),
                    scan_count=2,
                    risk_tier="low",
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/scan/backlog")
    def registry_scan_backlog(
        registry_source: Optional[str] = None,
        limit: int = 50,
        session: Session = Depends(get_session),
    ) -> RegistryScanBacklogResponse:
        backlog = get_registry_scan_backlog(session, registry_source, limit)
        return build_registry_scan_backlog_response(registry_source, backlog)

    response = TestClient(that_app).get(
        "/api/registry/scan/backlog",
        params={"registry_source": "seed", "limit": 50},
    )

    assert response.status_code == 200
    payload = response.json()
    backlog_ids = {item["server_id"] for item in payload["backlog"]}
    assert backlog_ids == {"never-scanned", "stale"}
    assert "current" not in backlog_ids
    assert payload["count"] == 2
    print("PASS")