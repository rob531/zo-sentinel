from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import sessionmaker, relationship, declarative_base, Session
from datetime import datetime
from typing import Optional
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
import os

router = APIRouter(prefix="/axis", tags=["axis"])

VALID_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


class TopServerResponse(BaseModel):
    server_id: int
    name: str
    axis: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    risk_tier: str
    scored_at: datetime


class TopServersListResponse(BaseModel):
    servers: list[TopServerResponse]


@router.get("/top-servers", response_model=TopServersListResponse)
def get_top_servers_by_axis(
    axis: Optional[str] = Query(default=None, description="Filter by specific axis (default: all)"),
    limit: int = Query(default=10, ge=1, le=100, description="Max results per axis"),
    sort: str = Query(default="critical", regex="^(critical|top_score)$"),
    session: Session = Depends(get_session),
):
    axes_to_query = [axis] if axis and axis in VALID_AXES else VALID_AXES

    if sort == "critical":
        order_clause = MCPLLMAxisScores.p_critical.desc()
    else:
        order_clause = MCPLLMAxisScores.p_top.asc()

    query = (
        session.query(
            MCPServerRegistry.id.label("server_id"),
            MCPServerRegistry.name,
            MCPLLMAxisScores.axis_name.label("axis"),
            MCPLLMAxisScores.axis_label.label("label"),
            MCPLLMAxisScores.p_top,
            MCPLLMAxisScores.p_critical,
            MCPLLMAxisScores.p_danger,
            MCPServerRegistry.risk_tier,
            MCPLLMAxisScores.scored_at,
        )
        .join(
            MCPLLMAxisScores,
            MCPServerRegistry.id == MCPLLMAxisScores.server_id,
        )
        .filter(MCPLLMAxisScores.axis_name.in_(axes_to_query))
        .order_by(order_clause)
        .limit(limit)
    )

    results = query.all()

    servers = [
        TopServerResponse(
            server_id=row.server_id,
            name=row.name,
            axis=row.axis,
            label=row.label,
            p_top=float(row.p_top),
            p_critical=float(row.p_critical),
            p_danger=float(row.p_danger),
            risk_tier=row.risk_tier,
            scored_at=row.scored_at,
        )
        for row in results
    ]

    return TopServersListResponse(servers=servers)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    import app.db
    import app.models

    SQLITE_URL = "sqlite:///:memory:"
    engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    app.models.Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.db.get_session = override_get_session

    db = TestingSessionLocal()

    server1 = MCPServerRegistry(
        name="high-risk-server",
        risk_tier="CRITICAL",
        risk_score=0.95,
    )
    db.add(server1)
    db.commit()
    db.refresh(server1)

    server2 = MCPServerRegistry(
        name="low-risk-server",
        risk_tier="LOW",
        risk_score=0.15,
    )
    db.add(server2)
    db.commit()
    db.refresh(server2)

    score_high = MCPLLMAxisScores(
        server_id=server1.id,
        axis_name="overall_risk",
        axis_label="Overall Risk Assessment",
        p_top=0.92,
        p_critical=0.08,
        p_danger=0.85,
        scored_at=datetime.utcnow(),
    )
    db.add(score_high)

    score_low = MCPLLMAxisScores(
        server_id=server2.id,
        axis_name="overall_risk",
        axis_label="Overall Risk Assessment",
        p_top=0.10,
        p_critical=0.90,
        p_danger=0.05,
        scored_at=datetime.utcnow(),
    )
    db.add(score_low)
    db.commit()
    db.close()

    app_main = FastAPI()
    app_main.include_router(router)

    with app.dependency_overrides.get(app.db.get_session, override_get_session):
        client = TestClient(app_main)
        response = client.get("/axis/top-servers?axis=overall_risk&limit=5")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    servers = data.get("servers", [])

    assert len(servers) == 2, f"Expected 2 servers, got {len(servers)}"

    p_criticals = [s["p_critical"] for s in servers]
    assert p_criticals == sorted(p_criticals, reverse=True), (
        f"Servers not sorted by p_critical descending: {p_criticals}"
    )

    print("PASS")