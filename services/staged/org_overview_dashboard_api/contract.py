from datetime import datetime
from typing import List, Dict
import random

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Org, McpServerRegistry, McpLlmAxisScore


class TopRiskServer(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    max_p_critical: float


class OrgOverviewResponse(BaseModel):
    org_id: int
    total_servers: int
    tier_counts: Dict[str, int]
    median_p_top: float
    top_risk_servers: List[TopRiskServer]


router = APIRouter()


@router.get("/api/orgs/{org_id}/overview", response_model=OrgOverviewResponse)
def get_org_overview(org_id: int, session: Session = Depends(get_session)):
    servers = session.query(McpServerRegistry).all()
    total_servers = len(servers)

    tier_counts: Dict[str, int] = {}
    for s in servers:
        tier = s.risk_tier or "unknown"
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    axis_scores = session.query(McpLlmAxisScore).join(
        McpServerRegistry,
        McpLlmAxisScore.server_id == McpServerRegistry.server_id
    ).filter(
        McpServerRegistry.server_id.in_([s.server_id for s in servers])
    ).all()

    p_tops = [a.p_top for a in axis_scores if a.p_top is not None]
    sorted_p_tops = sorted(p_tops)
    n = len(sorted_p_tops)
    median_p_top = sorted_p_tops[n // 2] if n % 2 == 1 else (sorted_p_tops[n // 2 - 1] + sorted_p_tops[n // 2]) / 2

    axis_by_server: Dict[str, List[McpLlmAxisScore]] = {}
    for a in axis_scores:
        if a.server_id not in axis_by_server:
            axis_by_server[a.server_id] = []
        axis_by_server[a.server_id].append(a)

    servers_with_max = []
    for s in servers:
        if s.server_id in axis_by_server:
            max_p = max(a.p_critical for a in axis_by_server[s.server_id] if a.p_critical is not None)
            servers_with_max.append((s, max_p))

    servers_with_max.sort(key=lambda x: x[1], reverse=True)

    top_risk_servers = [
        TopRiskServer(
            server_id=s.server_id,
            name=s.name,
            risk_tier=s.risk_tier or "unknown",
            max_p_critical=max_p
        )
        for s, max_p in servers_with_max[:5]
    ]

    return OrgOverviewResponse(
        org_id=org_id,
        total_servers=total_servers,
        tier_counts=tier_counts,
        median_p_top=median_p_top,
        top_risk_servers=top_risk_servers
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()

    orgs_data = [
        {"id": 1, "name": "Org A"},
        {"id": 2, "name": "Org B"},
        {"id": 3, "name": "Org C"},
    ]
    tiers = ["low", "medium", "high"]

    for org in orgs_data:
        db_org = Org(id=org["id"], name=org["name"])
        db.add(db_org)
        db.commit()
        for i in range(5):
            server = McpServerRegistry(
                server_id=f"{org['id']}_{i}",
                name=f"Server {i+1}",
                risk_tier=tiers[i % 3],
                url=f"http://server{i+1}.example.com",
                verdict="active",
                trust_score=50.0 + i * 10,
                confidence=0.8 + i * 0.04,
                first_seen=datetime.utcnow(),
                last_seen=datetime.utcnow(),
                last_scanned=datetime.utcnow(),
                last_assessed=datetime.utcnow(),
            )
            db.add(server)
            db.commit()
            for axis in ["security", "reliability", "performance"]:
                axis_score = McpLlmAxisScore(
                    id=f"{server.server_id}_{axis}",
                    server_id=server.server_id,
                    axis_name=axis,
                    p_critical=random.random(),
                    p_top=random.random(),
                    p_danger=random.random(),
                    probs="[0.2, 0.3, 0.5]",
                    label="test",
                    label_index=0,
                    model_version="v1",
                    decision_rule_version="v1",
                    adapter_sha256="sha256_test",
                    scored_at=datetime.utcnow(),
                )
                db.add(axis_score)
            db.commit()

    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: db
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/api/orgs/1/overview")
    data = response.json()

    try:
        assert response.status_code == 200
        assert sum(data["tier_counts"].values()) == data["total_servers"]
        assert len(data["top_risk_servers"]) <= 5
        print("PASS")
        exit(0)
    except AssertionError as e:
        print(f"FAIL: {e}")
        exit(1)