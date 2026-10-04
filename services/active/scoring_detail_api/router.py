# deps: fastapi, pydantic, sqlalchemy
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["scoring_detail_api"])


class AxisDetail(BaseModel):
    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: Optional[bool]
    escalated_to: Optional[str]


class ServerScoringDetailResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    risk_tier: Optional[str]
    verdict: Optional[str]
    overall_score: Optional[float]
    decision_rule_version: Optional[str]
    model_version: Optional[str]
    scored_at: Optional[datetime]
    axes: list[AxisDetail]
    criteria_version: str


@router.get("/scoring/{server_id}/detail", response_model=ServerScoringDetailResponse)
def scoring_detail(
    server_id: str,
    latest_only: bool = Query(False),
    db: Session = Depends(get_session),
) -> ServerScoringDetailResponse:
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    query = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id)
    if latest_only:
        subq = (
            db.query(
                McpLlmAxisScore.axis_name,
                func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
            )
            .filter(McpLlmAxisScore.server_id == server_id)
            .group_by(McpLlmAxisScore.axis_name)
            .subquery()
        )
        query = query.join(
            subq,
            (McpLlmAxisScore.axis_name == subq.c.axis_name)
            & (McpLlmAxisScore.scored_at == subq.c.max_scored_at),
        )
    axis_scores = query.all()

    if not axis_scores:
        raise HTTPException(status_code=404, detail="No scoring data found for server")

    overall = next((s for s in axis_scores if s.axis_name == "overall_risk"), None)
    if not overall:
        raise HTTPException(status_code=500, detail="Missing overall_risk axis")

    axes = [
        AxisDetail(
            axis_name=s.axis_name,
            label=s.label,
            label_index=s.label_index,
            p_top=s.p_top,
            p_critical=s.p_critical,
            p_danger=s.p_danger,
            escalated=s.escalated,
            escalated_to=s.escalated_to,
        )
        for s in axis_scores
    ]

    return ServerScoringDetailResponse(
        server_id=server.server_id,
        server_name=server.name,
        risk_tier=server.risk_tier,
        verdict=server.verdict,
        overall_score=overall.p_top,
        decision_rule_version=overall.decision_rule_version,
        model_version=overall.model_version,
        scored_at=overall.scored_at,
        axes=axes,
        criteria_version="1.0",
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, func
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    from app.models import Base

    Base.metadata.create_all(bind=engine)

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    test_app.dependency_overrides[get_session] = override_get_session

    # seed data
    now = datetime.now()
    with SessionLocal() as sess:
        srv = McpServerRegistry(
            server_id="srv1",
            name="Test Server",
            risk_tier="high",
            verdict="malicious",
        )
        sess.add(srv)
        sess.flush()

        for axis in [
            "overall_risk",
            "auth_strength",
            "capability_breadth",
            "data_sensitivity",
            "network_egress",
            "maintainer_trust",
            "exploit_surface",
        ]:
            sess.add(
                McpLlmAxisScore(
                    server_id="srv1",
                    axis_name=axis,
                    label="high",
                    label_index=2,
                    p_top=0.85,
                    p_critical=0.7,
                    p_danger=0.5,
                    decision_rule_version="1.0",
                    model_version="1.0",
                    scored_at=now,
                    escalated=False,
                )
            )
        sess.commit()

    client = TestClient(test_app)

    # happy path
    r = client.get("/api/scoring/srv1/detail")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    data = r.json()
    assert data["server_id"] == "srv1"
    assert data["risk_tier"] == "high"
    assert len(data["axes"]) == 7
    assert data["overall_score"] is not None

    # latest_only filter
    r2 = client.get("/api/scoring/srv1/detail?latest_only=true")
    assert r2.status_code == 200
    assert len(r2.json()["axes"]) == 7

    # not found
    r3 = client.get("/api/scoring/nobody/detail")
    assert r3.status_code == 404

    print("PASS")
