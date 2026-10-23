from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import get_session
from app.models import Base, McpServerRegistry, McpLlmAxisScore

router = APIRouter()

class ServerInfo(BaseModel):
    server_id: str
    name: str
    first_seen: datetime
    registry_source: str

class UnscoredBacklogResponse(BaseModel):
    total_unscored: int
    oldest_unscored_days: int
    servers: list[ServerInfo]

@router.get("/api/scoring/unscored-backlog", response_model=UnscoredBacklogResponse)
def get_unscored_backlog(session: Session = Depends(get_session)):
    unscored_servers = (
        session.query(McpServerRegistry)
        .outerjoin(McpLlmAxisScore, McpServerRegistry.server_id == McpLlmAxisScore.server_id)
        .filter(McpLlmAxisScore.server_id.is_(None))
        .all()
    )
    now = datetime.now(timezone.utc)
    oldest_unscored_days = 0
    for server in unscored_servers:
        if server.first_seen:
            age = (now - server.first_seen.replace(tzinfo=timezone.utc)).days
            if age > oldest_unscored_days:
                oldest_unscored_days = age
    return UnscoredBacklogResponse(
        total_unscored=len(unscored_servers),
        oldest_unscored_days=oldest_unscored_days,
        servers=[
            ServerInfo(
                server_id=s.server_id,
                name=s.name,
                first_seen=s.first_seen,
                registry_source=s.registry_source
            )
            for s in unscored_servers
        ]
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import get_session

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    client = TestClient(that_app)

    srv1 = McpServerRegistry(
        confidence=0.8, description="desc1", first_seen=datetime.now(timezone.utc) - timedelta(days=5),
        last_assessed=datetime.now(timezone.utc), last_scanned=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc), meta="{}", name="unscored1",
        registry_source="source1", risk_tier="low", scan_count=0, server_id="srv-001",
        trust_score=0.5, url="http://s1.example.com", verdict="unknown", verdict_reasoning="no data"
    )
    srv2 = McpServerRegistry(
        confidence=0.8, description="desc2", first_seen=datetime.now(timezone.utc) - timedelta(days=15),
        last_assessed=datetime.now(timezone.utc), last_scanned=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc), meta="{}", name="unscored2",
        registry_source="source2", risk_tier="low", scan_count=0, server_id="srv-002",
        trust_score=0.5, url="http://s2.example.com", verdict="unknown", verdict_reasoning="no data"
    )
    srv3 = McpServerRegistry(
        confidence=0.8, description="desc3", first_seen=datetime.now(timezone.utc) - timedelta(days=30),
        last_assessed=datetime.now(timezone.utc), last_scanned=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc), meta="{}", name="unscored3",
        registry_source="source3", risk_tier="low", scan_count=0, server_id="srv-003",
        trust_score=0.5, url="http://s3.example.com", verdict="unknown", verdict_reasoning="no data"
    )
    srv4 = McpServerRegistry(
        confidence=0.8, description="desc4", first_seen=datetime.now(timezone.utc) - timedelta(days=2),
        last_assessed=datetime.now(timezone.utc), last_scanned=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc), meta="{}", name="scored1",
        registry_source="source4", risk_tier="low", scan_count=0, server_id="srv-004",
        trust_score=0.5, url="http://s4.example.com", verdict="unknown", verdict_reasoning="no data"
    )
    srv5 = McpServerRegistry(
        confidence=0.8, description="desc5", first_seen=datetime.now(timezone.utc) - timedelta(days=1),
        last_assessed=datetime.now(timezone.utc), last_scanned=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc), meta="{}", name="scored2",
        registry_source="source5", risk_tier="low", scan_count=0, server_id="srv-005",
        trust_score=0.5, url="http://s5.example.com", verdict="unknown", verdict_reasoning="no data"
    )

    db = TestingSessionLocal()
    db.add(srv1)
    db.add(srv2)
    db.add(srv3)
    db.add(srv4)
    db.add(srv5)

    score1 = McpLlmAxisScore(
        adapter_sha256="sha1", axis_name="quality", decision_rule_version="v1",
        escalated=False, id=1, label="good", label_index=1, model_version="v1",
        p_critical=0.1, p_danger=0.2, p_top=0.7, probs="[0.1,0.2,0.7]",
        scored_at=datetime.now(timezone.utc), server_id="srv-004"
    )
    score2 = McpLlmAxisScore(
        adapter_sha256="sha2", axis_name="quality", decision_rule_version="v1",
        escalated=False, id=2, label="good", label_index=1, model_version="v1",
        p_critical=0.1, p_danger=0.2, p_top=0.7, probs="[0.1,0.2,0.7]",
        scored_at=datetime.now(timezone.utc), server_id="srv-005"
    )
    db.add(score1)
    db.add(score2)
    db.commit()
    db.close()

    response = client.get("/api/scoring/unscored-backlog")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["total_unscored"] == 3, f"Expected total_unscored=3, got {data['total_unscored']}"
    assert data["oldest_unscored_days"] >= 0, f"Expected oldest_unscored_days>=0, got {data['oldest_unscored_days']}"

    print("Response:", data)
    print("PASS")