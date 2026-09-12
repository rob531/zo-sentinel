from fastapi import FastAPI, APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict
from sqlalchemy.orm import Session
from datetime import datetime

from app.db import get_session
from app.models import McpLlmAxisScore, Base

router = APIRouter(prefix="/api")


class AxisDetail(BaseModel):
    label: str
    score: float


class ScoreBreakdown(BaseModel):
    axes: Dict[str, AxisDetail]
    overall: float
    risk_tier: str


@router.get("/servers/{server_id}/scores", response_model=ScoreBreakdown)
def get_server_score_breakdown(
    server_id: int, session: Session = Depends(get_session)
):
    rows = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Server not found")
    axes: Dict[str, AxisDetail] = {}
    overall_score = 0.0
    for row in rows:
        score = row.p_top
        axes[row.axis_name] = AxisDetail(label=row.label, score=score)
        if score > overall_score:
            overall_score = score
    if overall_score >= 0.8:
        tier = "critical"
    elif overall_score >= 0.5:
        tier = "danger"
    else:
        tier = "safe"
    return ScoreBreakdown(axes=axes, overall=overall_score, risk_tier=tier)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite for self‑test
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    Base.metadata.create_all(bind=engine)

    def get_test_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed test data
    axis_names = [
        "confidentiality",
        "integrity",
        "availability",
        "authenticity",
        "nonrepudiation",
        "privacy",
        "audit",
    ]
    with SessionLocal() as db:
        for i, name in enumerate(axis_names, start=1):
            row = McpLlmAxisScore(
                id=i,
                server_id=1,
                axis_name=name,
                label=f"Label {i}",
                adapter_sha256="sha256dummy",
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                label_index=i,
                model_version="m1",
                p_critical=0.0,
                p_danger=0.0,
                p_top=0.1 * i,
                probs="{}",
                scored_at=datetime.utcnow(),
            )
            db.add(row)
        db.commit()

    client = TestClient(app)
    resp = client.get("/api/servers/1/scores")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert "axes" in data and isinstance(data["axes"], dict)
    assert len(data["axes"]) == 7
    assert "overall" in data
    assert "risk_tier" in data
    print("PASS")