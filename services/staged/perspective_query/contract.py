from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, Perspective, PerspectiveSnapshot

router = APIRouter(prefix="/api", tags=["perspectives"])


class ServerResponse(BaseModel):
    server_id: str
    name: str
    risk_tier: Optional[str]
    last_scanned: Optional[datetime]
    composite_score: Optional[float]

    class Config:
        from_attributes = True


class PerspectiveServersResponse(BaseModel):
    perspective_id: int
    name: str
    server_count: int
    servers: list[ServerResponse]


def _compute_composite(score: Optional[McpLlmAxisScore]) -> Optional[float]:
    if score is None:
        return None
    if score.p_critical is not None:
        return score.p_critical
    if score.p_danger is not None:
        return score.p_danger
    if score.p_top is not None:
        return score.p_top
    return None


@router.get("/perspectives/{perspective_id}/servers", response_model=PerspectiveServersResponse)
async def get_perspective_servers(
    perspective_id: int,
    session: Session = Depends(get_session)
) -> PerspectiveServersResponse:
    perspective = session.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")

    snapshot = (
        session.query(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at.desc())
        .first()
    )
    if not snapshot:
        return PerspectiveServersResponse(
            perspective_id=perspective_id,
            name=perspective.name,
            server_count=0,
            servers=[]
        )

    membership: dict[str, Any] = {}
    if snapshot.membership:
        if isinstance(snapshot.membership, dict):
            membership = snapshot.membership
        elif isinstance(snapshot.membership, str):
            membership = json.loads(snapshot.membership)

    member_server_ids: list[str] = membership.get("server_ids", [])

    facet_filters: dict[str, Any] = {}
    if perspective.facet_filters:
        if isinstance(perspective.facet_filters, dict):
            facet_filters = perspective.facet_filters
        elif isinstance(perspective.facet_filters, str):
            facet_filters = json.loads(perspective.facet_filters)

    server_query = session.query(McpServerRegistry)

    if member_server_ids:
        server_query = server_query.filter(McpServerRegistry.server_id.in_(member_server_ids))

    for key, value in facet_filters.items():
        col = getattr(McpServerRegistry, key, None)
        if col is not None:
            server_query = server_query.filter(col == value)

    servers = server_query.all()

    servers_with_scores: list[ServerResponse] = []
    for srv in servers:
        axis_score = (
            session.query(McpLlmAxisScore)
            .filter(McpLlmAxisScore.server_id == srv.server_id)
            .order_by(McpLlmAxisScore.scored_at.desc())
            .first()
        )
        servers_with_scores.append(
            ServerResponse(
                server_id=srv.server_id,
                name=srv.name,
                risk_tier=srv.risk_tier,
                last_scanned=srv.last_scanned,
                composite_score=_compute_composite(axis_score)
            )
        )

    return PerspectiveServersResponse(
        perspective_id=perspective_id,
        name=perspective.name,
        server_count=len(servers_with_scores),
        servers=servers_with_scores
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool
    from app.models import Base as AppBase

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    AppBase.metadata.create_all(bind=test_engine)

    session = TestingSessionLocal()

    now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    p1 = Perspective(
        id=1,
        name="Production Servers",
        org_id=1,
        created_by=1,
        description="Prod perspective",
        facet_filters=json.dumps({}),
        created_at=now,
        updated_at=now
    )
    p2 = Perspective(
        id=2,
        name="Staging Servers",
        org_id=1,
        created_by=1,
        description="Staging perspective",
        facet_filters=json.dumps({}),
        created_at=now,
        updated_at=now
    )
    session.add_all([p1, p2])

    s1 = PerspectiveSnapshot(
        id=1,
        perspective_id=1,
        taken_at=now,
        membership=json.dumps({"server_ids": ["srv-001", "srv-002", "srv-003"]})
    )
    s2 = PerspectiveSnapshot(
        id=2,
        perspective_id=2,
        taken_at=now,
        membership=json.dumps({"server_ids": ["srv-002", "srv-003"]})
    )
    session.add_all([s1, s2])

    srv1 = McpServerRegistry(
        server_id="srv-001",
        name="Server Alpha",
        registry_source="test",
        risk_tier="low",
        last_scanned=now,
        last_seen=now,
        first_seen=now,
        last_assessed=now,
        confidence=0.95,
        scan_count=5,
        trust_score=0.8,
        meta="{}",
        verdict="approved",
        verdict_reasoning="OK"
    )
    srv2 = McpServerRegistry(
        server_id="srv-002",
        name="Server Beta",
        registry_source="test",
        risk_tier="medium",
        last_scanned=now,
        last_seen=now,
        first_seen=now,
        last_assessed=now,
        confidence=0.85,
        scan_count=3,
        trust_score=0.7,
        meta="{}",
        verdict="approved",
        verdict_reasoning="OK"
    )
    srv3 = McpServerRegistry(
        server_id="srv-003",
        name="Server Gamma",
        registry_source="test",
        risk_tier="high",
        last_scanned=now,
        last_seen=now,
        first_seen=now,
        last_assessed=now,
        confidence=0.75,
        scan_count=2,
        trust_score=0.5,
        meta="{}",
        verdict="pending",
        verdict_reasoning="Review needed"
    )
    session.add_all([srv1, srv2, srv3])

    ax1 = McpLlmAxisScore(
        id=1,
        server_id="srv-001",
        adapter_sha256="abc123",
        axis_name="risk",
        model_version="v1",
        decision_rule_version="r1",
        label="low",
        label_index=0,
        p_critical=0.05,
        p_danger=0.10,
        p_top=0.85,
        probs="[0.05, 0.10, 0.85]",
        scored_at=now,
        escalated=False,
        escalated_to=None
    )
    ax2 = McpLlmAxisScore(
        id=2,
        server_id="srv-002",
        adapter_sha256="def456",
        axis_name="risk",
        model_version="v1",
        decision_rule_version="r1",
        label="medium",
        label_index=1,
        p_critical=0.20,
        p_danger=0.45,
        p_top=0.35,
        probs="[0.20, 0.45, 0.35]",
        scored_at=now,
        escalated=False,
        escalated_to=None
    )
    ax3 = McpLlmAxisScore(
        id=3,
        server_id="srv-003",
        adapter_sha256="ghi789",
        axis_name="risk",
        model_version="v1",
        decision_rule_version="r1",
        label="high",
        label_index=2,
        p_critical=0.70,
        p_danger=0.20,
        p_top=0.10,
        probs="[0.70, 0.20, 0.10]",
        scored_at=now,
        escalated=False,
        escalated_to=None
    )
    session.add_all([ax1, ax2, ax3])

    session.commit()
    session.close()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)

    that_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(that_app)

    r1 = client.get("/api/perspectives/1/servers")
    assert r1.status_code == 200, f"Expected 200, got {r1.status_code}"
    d1 = r1.json()
    assert d1["perspective_id"] == 1
    assert d1["name"] == "Production Servers"
    assert d1["server_count"] == 3, f"Expected 3, got {d1['server_count']}"
    assert len(d1["servers"]) == 3

    r2 = client.get("/api/perspectives/2/servers")
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["server_count"] == 2, f"Expected 2, got {d2['server_count']}"

    r404 = client.get("/api/perspectives/999/servers")
    assert r404.status_code == 404

    print("PASS")