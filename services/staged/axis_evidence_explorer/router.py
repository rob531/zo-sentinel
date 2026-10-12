# services/staged/axis_evidence_explorer/router.py
from fastapi import APIRouter, Depends, Path, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore
from pydantic import BaseModel
from typing import List
from datetime import datetime
import json

router = APIRouter(prefix="/api", tags=["axis_evidence"])


class EvidenceBlob(BaseModel):
    axis: str
    p_top: float
    label: str
    decision_rule_version: str


class AxisEvidenceResponse(BaseModel):
    server_id: str
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    scored_at: str
    probs: List[float]
    evidence_blob: EvidenceBlob

    class Config:
        from_attributes = True


def get_evidence(server_id: str, axis_name: str, db: Session = Depends(get_session)) -> AxisEvidenceResponse:
    score = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == axis_name
    ).first()

    if not score:
        raise HTTPException(status_code=404, detail="Axis evidence not found")

    probs = score.probs
    if isinstance(probs, str):
        probs = json.loads(probs)

    scored_at = score.scored_at
    if isinstance(scored_at, datetime):
        scored_at = scored_at.isoformat()

    return AxisEvidenceResponse(
        server_id=score.server_id,
        axis_name=score.axis_name,
        label=score.label,
        p_top=score.p_top,
        p_critical=score.p_critical,
        p_danger=score.p_danger,
        escalated=score.escalated,
        scored_at=scored_at,
        probs=probs or [],
        evidence_blob=EvidenceBlob(
            axis=score.axis_name,
            p_top=score.p_top,
            label=score.label,
            decision_rule_version=score.decision_rule_version or ""
        )
    )


@router.get("/servers/{server_id}/axes/{axis_name}/evidence", response_model=AxisEvidenceResponse)
def get_axis_evidence(
    server_id: str = Path(...),
    axis_name: str = Path(...),
    db: Session = Depends(get_session)
) -> AxisEvidenceResponse:
    return get_evidence(server_id, axis_name, db)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

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

    with TestingSessionLocal() as db:
        score1 = McpLlmAxisScore(
            id=1,
            server_id="test-srv-001",
            axis_name="trust",
            label="high",
            label_index=0,
            p_top=0.85,
            p_critical=0.05,
            p_danger=0.10,
            probs=json.dumps([0.85, 0.10, 0.05]),
            escalated=False,
            escalated_to=None,
            decision_rule_version="v1.0",
            model_version="gpt-4",
            adapter_sha256="abc123",
            scored_at=datetime(2024, 1, 15, 10, 30, 0)
        )
        score2 = McpLlmAxisScore(
            id=2,
            server_id="test-srv-001",
            axis_name="risk",
            label="medium",
            label_index=1,
            p_top=0.45,
            p_critical=0.25,
            p_danger=0.30,
            probs=json.dumps([0.45, 0.30, 0.25]),
            escalated=True,
            escalated_to="security-team",
            decision_rule_version="v1.0",
            model_version="gpt-4",
            adapter_sha256="def456",
            scored_at=datetime(2024, 1, 15, 11, 0, 0)
        )
        db.add(score1)
        db.add(score2)
        db.commit()

    from fastapi import FastAPI
    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(that_app)

    r = client.get("/api/servers/test-srv-001/axes/trust/evidence")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    data = r.json()
    assert isinstance(data["p_top"], float), f"p_top should be float, got {type(data['p_top'])}"
    scored_at_dt = datetime.fromisoformat(data["scored_at"])
    assert data["scored_at"] == scored_at_dt.strftime("%Y-%m-%dT%H:%M:%S")
    assert data["probs"] == [0.85, 0.10, 0.05]
    assert data["evidence_blob"]["axis"] == "trust"

    r2 = client.get("/api/servers/test-srv-001/axes/risk/evidence")
    assert r2.status_code == 200
    assert r2.json()["escalated"] is True

    print("PASS")