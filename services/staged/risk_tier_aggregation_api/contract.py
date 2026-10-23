from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from typing import List

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter()


class ServerDetail(BaseModel):
    server_id: int
    name: str
    risk_tier: str
    overall_risk: float


class TierInfo(BaseModel):
    tier: str
    count: int
    servers: List[ServerDetail]


class AggregationResponse(BaseModel):
    tiers: List[TierInfo]


def compute_risk_aggregation(db: Session) -> AggregationResponse:
    query = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            func.min(McpLlmAxisScore.p_critical).label("overall_risk"),
        )
        .join(McpLlmAxisScore, McpServerRegistry.server_id == McpLlmAxisScore.server_id)
        .group_by(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .order_by(McpServerRegistry.risk_tier)
    )

    servers_by_tier = {}
    for row in query.all():
        tier = row.risk_tier or "unknown"
        if tier not in servers_by_tier:
            servers_by_tier[tier] = []
        servers_by_tier[tier].append(
            ServerDetail(
                server_id=row.server_id,
                name=row.name,
                risk_tier=row.risk_tier,
                overall_risk=row.overall_risk,
            )
        )

    tier_order = ["critical", "high", "medium", "low", "unknown"]
    tiers = [
        TierInfo(tier=tier, count=len(servers), servers=servers)
        for tier in tier_order
        if tier in servers_by_tier
        for servers in [servers_by_tier[tier]]
    ]

    return AggregationResponse(tiers=tiers)


@router.get("/risk/aggregation", response_model=AggregationResponse)
def get_risk_aggregation(db: Session = Depends(get_session)):
    return compute_risk_aggregation(db)


def main():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE mcp_server_registry (server_id INTEGER PRIMARY KEY, name TEXT, risk_tier TEXT)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE mcp_llm_axis_scores (scored_at TIMESTAMP, server_id INTEGER, p_critical REAL, axis_name TEXT)"
        )

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO mcp_server_registry VALUES (:s, :n, :t)"), [
            {"s": 1, "n": "Server 1", "t": "critical"},
            {"s": 2, "n": "Server 2", "t": "high"},
            {"s": 3, "n": "Server 3", "t": "medium"},
            {"s": 4, "n": "Server 4", "t": "low"},
            {"s": 5, "n": "Server 5", "t": "critical"},
            {"s": 6, "n": "Server 6", "t": "critical"},
        ])
        conn.execute(text("INSERT INTO mcp_llm_axis_scores VALUES (:s, :sid, :p, :a)"), [
            {"s": "2024-01-01", "sid": 1, "p": 0.9, "a": "security"},
            {"s": "2024-01-01", "sid": 2, "p": 0.7, "a": "security"},
            {"s": "2024-01-01", "sid": 3, "p": 0.5, "a": "security"},
            {"s": "2024-01-01", "sid": 4, "p": 0.1, "a": "security"},
            {"s": "2024-01-01", "sid": 5, "p": 0.85, "a": "security"},
            {"s": "2024-01-01", "sid": 6, "p": 0.95, "a": "security"},
        ])

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/risk/aggregation")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()

    tier_counts = {t["tier"]: t["count"] for t in data["tiers"]}
    assert tier_counts.get("critical") == 3, f"Expected 3 critical, got {tier_counts.get('critical')}"
    assert tier_counts.get("high") == 1, f"Expected 1 high, got {tier_counts.get('high')}"
    assert tier_counts.get("medium") == 1, f"Expected 1 medium, got {tier_counts.get('medium')}"
    assert tier_counts.get("low") == 1, f"Expected 1 low, got {tier_counts.get('low')}"

    print("PASS")


if __name__ == "__main__":
    main()