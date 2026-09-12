from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select, exists
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore


class UnscoredServerSample(BaseModel):
    server_id: str
    name: str
    first_seen: datetime
    registry_source: str


class ScoringGapResponse(BaseModel):
    total_servers: int
    scored_count: int
    unscored_count: int
    unscored_sample: List[UnscoredServerSample]


router = APIRouter(prefix="/api/scoring", tags=["scoring"])


@router.get("/gap", response_model=ScoringGapResponse)
def get_scoring_gap(db: Session = Depends(get_session)) -> ScoringGapResponse:
    total_servers = db.query(func.count(McpServerRegistry.server_id)).scalar()

    scored_count_subq = (
        select(McpLlmAxisScore.server_id)
        .distinct()
        .subquery()
    )
    scored_count = (
        db.query(func.count(scored_count_subq.c.server_id))
        .scalar()
    )

    unscored_count = total_servers - scored_count

    unscored_servers_query = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.first_seen,
            McpServerRegistry.registry_source,
        )
        .outerjoin(
            McpLlmAxisScore,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .filter(McpLlmAxisScore.id.is_(None))
        .limit(100)
    )
    unscored_servers = unscored_servers_query.all()

    unscored_sample = [
        UnscoredServerSample(
            server_id=s.server_id,
            name=s.name,
            first_seen=s.first_seen,
            registry_source=s.registry_source,
        )
        for s in unscored_servers
    ]

    return ScoringGapResponse(
        total_servers=total_servers,
        scored_count=scored_count,
        unscored_count=unscored_count,
        unscored_sample=unscored_sample,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from datetime import datetime
    import json

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base
    Base.metadata.create_all(test_engine)

    TestingSessionLocal = sessionmaker(bind=test_engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    from app.models import McpServerRegistry, McpLlmAxisScore

    db = TestingSessionLocal()

    s1 = McpServerRegistry(
        server_id="srv-001",
        name="Server Alpha",
        first_seen=datetime(2024, 1, 15),
        registry_source="github",
        url="https://example.com/alpha",
    )
    s2 = McpServerRegistry(
        server_id="srv-002",
        name="Server Beta",
        first_seen=datetime(2024, 2, 20),
        registry_source="npm",
        url="https://example.com/beta",
    )
    s3 = McpServerRegistry(
        server_id="srv-003",
        name="Server Gamma",
        first_seen=datetime(2024, 3, 10),
        registry_source="docker",
        url="https://example.com/gamma",
    )
    db.add_all([s1, s2, s3])
    db.commit()

    axis_score = McpLlmAxisScore(
        server_id="srv-001",
        axis_name="safety",
        model_version="v1",
        label="safe",
        label_index=0,
        probs=json.dumps([0.9, 0.1]),
        p_critical=0.05,
        p_danger=0.05,
        p_top=0.9,
        scored_at=datetime.utcnow(),
        decision_rule_version="1.0",
    )
    db.add(axis_score)
    db.commit()
    db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    client = TestClient(that_app)
    response = client.get("/api/scoring/gap")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()

    assert data["unscored_count"] == 2, f"Expected unscored_count==2, got {data['unscored_count']}"
    assert data["scored_count"] == 1, f"Expected scored_count==1, got {data['scored_count']}"
    assert data["total_servers"] == 3, f"Expected total_servers==3, got {data['total_servers']}"

    print("PASS")