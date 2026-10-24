from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


class ServerStat(BaseModel):
    server_id: str
    name: str
    registry_source: Optional[str]
    risk_tier: Optional[str]
    association_count: int
    distinct_threat_types: int
    latest_at: datetime


class TopAssociationsResponse(BaseModel):
    generated_at: datetime
    window_days: int
    total: int
    servers: List[ServerStat]


@router.get(
    "/threat/associations/top",
    response_model=TopAssociationsResponse,
    name="get_top_threat_associations",
)
def get_top_associations(
    days: int = 30,
    limit: int = 50,
    session: Session = Depends(get_session),
) -> TopAssociationsResponse:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    query = text(
        """
        SELECT
            s.server_id,
            s.name,
            s.registry_source,
            s.risk_tier,
            COUNT(*) AS association_count,
            COUNT(DISTINCT ta.threat_type) AS distinct_threat_types,
            MAX(ta.timestamp) AS latest_at
        FROM mcp_threat_associations ta
        JOIN mcp_server_registry s ON ta.server_id = s.server_id
        WHERE ta.timestamp >= :cutoff
        GROUP BY s.server_id, s.name, s.registry_source, s.risk_tier
        ORDER BY association_count DESC, latest_at DESC
        LIMIT :limit
        """
    )
    rows = session.execute(query, {"cutoff": cutoff, "limit": limit}).fetchall()

    total_q = text(
        "SELECT COUNT(*) FROM mcp_threat_associations WHERE timestamp >= :cutoff"
    )
    total = session.execute(total_q, {"cutoff": cutoff}).scalar() or 0

    servers = [
        ServerStat(
            server_id=row.server_id,
            name=row.name,
            registry_source=row.registry_source,
            risk_tier=row.risk_tier,
            association_count=row.association_count,
            distinct_threat_types=row.distinct_threat_types,
            latest_at=row.latest_at,
        )
        for row in rows
    ]

    return TopAssociationsResponse(
        generated_at=now,
        window_days=days,
        total=total,
        servers=servers,
    )


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import (
        Column,
        DateTime,
        Integer,
        MetaData,
        String,
        Table,
        create_engine,
    )
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()

    server_tbl = Table(
        "mcp_server_registry",
        metadata,
        Column("server_id", String, primary_key=True),
        Column("name", String),
        Column("registry_source", String),
        Column("risk_tier", String),
    )
    threat_tbl = Table(
        "mcp_threat_associations",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("server_id", String),
        Column("threat_type", String),
        Column("timestamp", DateTime),
    )
    metadata.create_all(engine)

    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            server_tbl.insert(),
            [
                {
                    "server_id": "srv1",
                    "name": "Server One",
                    "registry_source": "srcA",
                    "risk_tier": "high",
                },
                {
                    "server_id": "srv2",
                    "name": "Server Two",
                    "registry_source": "srcB",
                    "risk_tier": "low",
                },
            ],
        )
        conn.execute(
            threat_tbl.insert(),
            [
                {
                    "server_id": "srv1",
                    "threat_type": "typeA",
                    "timestamp": now - timedelta(days=1),
                },
                {
                    "server_id": "srv1",
                    "threat_type": "typeB",
                    "timestamp": now - timedelta(days=2),
                },
                {
                    "server_id": "srv1",
                    "threat_type": "typeA",
                    "timestamp": now - timedelta(days=3),
                },
                {
                    "server_id": "srv2",
                    "threat_type": "typeC",
                    "timestamp": now - timedelta(days=1),
                },
            ],
        )

    TestSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    response = client.get("/api/threat/associations/top?days=30&limit=2")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert len(payload["servers"]) == 2
    top_server = payload["servers"][0]
    assert top_server["server_id"] == "srv1"
    assert top_server["association_count"] == 3
    print("PASS")