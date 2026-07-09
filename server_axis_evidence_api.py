from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sqlalchemy.orm import Session

# App data layer imports (must remain unchanged)
from app.db import get_session, Base
from app.models import MCPServerRegistry, MCPLLMAxisScore

router = APIRouter()


class AxisEvidence(BaseModel):
    axis_name: str
    label: str
    label_index: int
    probs: List[float]
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    escalated_to: Optional[str]
    decision_rule_version: str
    model_version: str
    adapter_sha256: str
    scored_at: datetime

    class Config:
        orm_mode = True


class ServerAxisEvidenceResponse(BaseModel):
    server_id: str
    server_name: str
    verdict: str
    criteria_version: str
    axes: List[AxisEvidence]

    class Config:
        orm_mode = True


@router.get(
    "/servers/{server_id}/axis-evidence",
    response_model=ServerAxisEvidenceResponse,
)
def get_axis_evidence(
    server_id: str,
    db: Session = Depends(get_session),
):
    server = (
        db.query(MCPServerRegistry)
        .filter(MCPServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    scores = (
        db.query(MCPLLMAxisScore)
        .filter(MCPLLMAxisScore.server_id == server_id)
        .all()
    )

    axes = [
        AxisEvidence(
            axis_name=s.axis_name,
            label=s.label,
            label_index=s.label_index,
            probs=s.probs,
            p_top=s.p_top,
            p_critical=s.p_critical,
            p_danger=s.p_danger,
            escalated=s.escalated,
            escalated_to=s.escalated_to,
            decision_rule_version=s.decision_rule_version,
            model_version=s.model_version,
            adapter_sha256=s.adapter_sha256,
            scored_at=s.scored_at,
        )
        for s in scores
    ]

    return ServerAxisEvidenceResponse(
        server_id=server.server_id,
        server_name=server.server_name,
        verdict=server.verdict,
        criteria_version=server.criteria_version,
        axes=axes,
    )


if __name__ == "__main__":
    # Self‑test using an in‑memory SQLite DB
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create in‑memory DB and bind the app models
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(engine)

    # Seed test data
    db: Session = SessionLocal()
    test_server = MCPServerRegistry(
        server_id="test-id",
        server_name="Test Server",
        verdict="OK",
        criteria_version="v1",
    )
    db.add(test_server)

    for i in range(7):
        score = MCPLLMAxisScore(
            server_id="test-id",
            axis_name=f"axis_{i}",
            label="label",
            label_index=i,
            probs=[0.1, 0.2, 0.3],
            p_top=0.3,
            p_critical=0.2,
            p_danger=0.1,
            escalated=False,
            escalated_to=None,
            decision_rule_version="dr1",
            model_version="m1",
            adapter_sha256="sha256",
            scored_at=datetime.utcnow(),
        )
        db.add(score)

    db.commit()
    db.close()

    # Build FastAPI app
    app = FastAPI()
    app.include_router(router)

    # Override the session dependency with the in‑memory session factory
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    client = TestClient(app)

    response = client.get("/servers/test-id/axis-evidence")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()
    assert payload["server_id"] == "test-id"
    assert len(payload["axes"]) == 7
    for axis in payload["axes"]:
        assert axis["probs"] is not None

    print("PASS")