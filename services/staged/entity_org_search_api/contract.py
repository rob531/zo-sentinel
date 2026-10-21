from typing import Annotated, Optional

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, Org, User


class ServerResponse(BaseModel):
    server_id: str
    name: str
    url: str
    risk_tier: Optional[str]
    last_scanned: Optional[str]


class ServersListResponse(BaseModel):
    servers: list[ServerResponse]


app = FastAPI(title="entity_org_search_api")


@app.get("/api/servers", response_model=ServersListResponse)
def get_servers(
    org_id: Annotated[int, Query(description="Organization ID")],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    session: Session = Depends(get_session),
) -> ServersListResponse:
    """Get servers for an organization by joining users to mcp_server_registry."""
    offset = (page - 1) * page_size

    query = (
        text("""
            SELECT DISTINCT
                msr.server_id,
                msr.name,
                msr.url,
                msr.risk_tier,
                msr.last_scanned
            FROM mcp_server_registry msr
            INNER JOIN users u ON u.org_id = :org_id
            WHERE msr.server_id IN (
                SELECT DISTINCT server_id
                FROM users
                WHERE org_id = :org_id
            )
            ORDER BY msr.name
            LIMIT :page_size OFFSET :offset
        """)
    )

    result = session.execute(
        query,
        {"org_id": org_id, "page_size": page_size, "offset": offset},
    )
    rows = result.fetchall()

    servers = [
        ServerResponse(
            server_id=row.server_id,
            name=row.name,
            url=row.url,
            risk_tier=row.risk_tier,
            last_scanned=str(row.last_scanned) if row.last_scanned else None,
        )
        for row in rows
    ]

    return ServersListResponse(servers=servers)


if __name__ == "__main__":
    from sqlalchemy import Column, DateTime, Integer, String, Table, MetaData

    metadata = MetaData()

    test_users = Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("org_id", Integer, nullable=False),
        Column("server_id", String),
        Column("email", String),
        Column("created_at", DateTime),
        Column("clerk_id", String),
        Column("clerk_created_at", DateTime),
        Column("clerk_synced_via", String),
        Column("password_hash", String),
        Column("role", String),
    )

    test_servers = Table(
        "mcp_server_registry",
        metadata,
        Column("server_id", String, primary_key=True),
        Column("name", String),
        Column("url", String),
        Column("risk_tier", String),
        Column("last_scanned", DateTime),
        Column("confidence", String),
        Column("description", String),
        Column("first_seen", DateTime),
        Column("last_assessed", DateTime),
        Column("last_seen", DateTime),
        Column("meta", String),
        Column("registry_source", String),
        Column("scan_count", Integer),
        Column("trust_score", String),
        Column("verdict", String),
        Column("verdict_reasoning", String),
    )

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    from datetime import datetime

    session.execute(
        test_users.insert(),
        [
            {"id": 1, "org_id": 100, "server_id": "srv1", "email": "user1@org100.com"},
            {"id": 2, "org_id": 100, "server_id": "srv2", "email": "user2@org100.com"},
            {"id": 3, "org_id": 200, "server_id": "srv3", "email": "user1@org200.com"},
        ],
    )

    session.execute(
        test_servers.insert(),
        [
            {
                "server_id": "srv1",
                "name": "Server Alpha",
                "url": "https://alpha.example.com",
                "risk_tier": "low",
                "last_scanned": datetime(2024, 1, 15, 10, 30, 0),
                "confidence": "high",
                "description": "Alpha server",
                "first_seen": datetime(2024, 1, 1),
                "last_assessed": datetime(2024, 1, 15),
                "last_seen": datetime(2024, 1, 15),
                "meta": "{}",
                "registry_source": "test",
                "scan_count": 5,
                "trust_score": "85",
                "verdict": "approved",
                "verdict_reasoning": "passed checks",
            },
            {
                "server_id": "srv2",
                "name": "Server Beta",
                "url": "https://beta.example.com",
                "risk_tier": "medium",
                "last_scanned": datetime(2024, 1, 14, 12, 0, 0),
                "confidence": "high",
                "description": "Beta server",
                "first_seen": datetime(2024, 1, 2),
                "last_assessed": datetime(2024, 1, 14),
                "last_seen": datetime(2024, 1, 14),
                "meta": "{}",
                "registry_source": "test",
                "scan_count": 3,
                "trust_score": "70",
                "verdict": "pending",
                "verdict_reasoning": "needs review",
            },
            {
                "server_id": "srv3",
                "name": "Server Gamma",
                "url": "https://gamma.example.com",
                "risk_tier": "high",
                "last_scanned": datetime(2024, 1, 13, 8, 45, 0),
                "confidence": "medium",
                "description": "Gamma server",
                "first_seen": datetime(2024, 1, 3),
                "last_assessed": datetime(2024, 1, 13),
                "last_seen": datetime(2024, 1, 13),
                "meta": "{}",
                "registry_source": "test",
                "scan_count": 2,
                "trust_score": "40",
                "verdict": "rejected",
                "verdict_reasoning": "failed checks",
            },
        ],
    )
    session.commit()

    def override_get_session():
        yield session

    test_app = FastAPI()
    test_app.include_router(app.router)

    from fastapi.testclient import TestClient

    client = TestClient(test_app)
    client.app.dependency_overrides[get_session] = override_get_session

    response = client.get("/api/servers?org_id=100")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert len(data["servers"]) == 2, f"Expected 2 servers for org 100, got {len(data['servers'])}"

    response = client.get("/api/servers?org_id=200")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert len(data["servers"]) == 1, f"Expected 1 server for org 200, got {len(data['servers'])}"

    print("PASS")