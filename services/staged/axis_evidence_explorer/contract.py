from fastapi import FastAPI, Path, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from typing import List
from datetime import datetime
import json

from app.db import get_session
from app.models import McpLlmAxisScore
from pydantic import BaseModel


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
    evidence_blob: dict

    class Config:
        from_attributes = True


app = FastAPI()


def get_axis_evidence_logic(session: Session, server_id: str, axis_name: str) -> dict:
    record = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == axis_name
    ).first()

    if not record:
        return None

    probs_value = record.probs
    if isinstance(probs_value, str):
        probs_list = json.loads(probs_value)
    elif isinstance(probs_value, list):
        probs_list = probs_value
    else:
        probs_list = []

    scored_at_value = record.scored_at
    if isinstance(scored_at_value, datetime):
        scored_at_str = scored_at_value.isoformat()
    else:
        scored_at_str = str(scored_at_value)

    return {
        "server_id": record.server_id,
        "axis_name": record.axis_name,
        "label": record.label,
        "p_top": record.p_top,
        "p_critical": record.p_critical,
        "p_danger": record.p_danger,
        "escalated": record.escalated,
        "scored_at": scored_at_str,
        "probs": probs_list,
        "evidence_blob": {
            "axis": record.axis_name,
            "p_top": record.p_top,
            "label": record.label,
            "decision_rule_version": record.decision_rule_version
        }
    }


@app.get("/api/servers/{server_id}/axes/{axis_name}/evidence", response_model=AxisEvidenceResponse)
async def get_axis_evidence(
    server_id: str = Path(...),
    axis_name: str = Path(...),
    session: Session = Depends(get_session)
):
    result = get_axis_evidence_logic(session, server_id, axis_name)
    if result is None:
        raise HTTPException(status_code=404, detail="Axis evidence not found")
    return result


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    db = SessionLocal()
    score1 = McpLlmAxisScore(
        id=1,
        server_id="srv-001",
        axis_name="risk",
        label="medium",
        label_index=2,
        probs=json.dumps([0.1, 0.3, 0.5, 0.1]),
        p_top=0.5,
        p_critical=0.1,
        p_danger=0.3,
        escalated=False,
        escalated_to=None,
        decision_rule_version="v1",
        model_version="gpt-4",
        adapter_sha256="abc123",
        scored_at=datetime.now()
    )
    score2 = McpLlmAxisScore(
        id=2,
        server_id="srv-002",
        axis_name="risk",
        label="low",
        label_index=0,
        probs=json.dumps([0.7, 0.2, 0.08, 0.02]),
        p_top=0.7,
        p_critical=0.02,
        p_danger=0.08,
        escalated=False,
        escalated_to=None,
        decision_rule_version="v1",
        model_version="gpt-4",
        adapter_sha256="def456",
        scored_at=datetime.now()
    )
    db.add(score1)
    db.add(score2)
    db.commit()
    db.close()

    response = client.get("/api/servers/srv-001/axes/risk/evidence")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert isinstance(data["p_top"], float), f"p_top should be float, got {type(data['p_top'])}"

    from datetime import datetime
    try:
        datetime.fromisoformat(data["scored_at"])
    except (ValueError, TypeError):
        raise AssertionError(f"scored_at should be ISO format, got {data['scored_at']}")

    print("PASS")