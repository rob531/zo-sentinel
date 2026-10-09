# services/staged/axis_score_evidence_api/logic.py
from datetime import datetime
from typing import List, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, validator
from sqlalchemy import select
from sqlalchemy.orm import Session

# Real application data layer
from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter()


class AxisScoreEvidence(BaseModel):
    axis_name: str
    probs: Any
    p_top: float = Field(..., ge=0.0, le=1.0)
    p_critical: float = Field(..., ge=0.0, le=1.0)
    p_danger: float = Field(..., ge=0.0, le=1.0)
    label: str
    scored_at: datetime

    @validator("scored_at", pre=True)
    def ensure_datetime(cls, v):
        if isinstance(v, str):
            return datetime.fromisoformat(v)
        return v


@router.get(
    "/api/servers/{server_id}/axis-evidence",
    response_model=List[AxisScoreEvidence],
)
def get_axis_score_evidence(
    server_id: int,
    db: Session = Depends(get_session),
):
    stmt = (
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.axis_name)
    )
    rows = db.execute(stmt).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="No axis scores found for server")
    result = [
        AxisScoreEvidence(
            axis_name=row.axis_name,
            probs=row.probs,
            p_top=row.p_top,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
            label=row.label,
            scored_at=row.scored_at,
        )
        for row in rows
    ]
    return result


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build an in‑memory SQLite DB that mirrors the real models
    TEST_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Import Base from the same module that defines the models
    from app.db import Base  # type: ignore

    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Dependency override
    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed test data: 7 axes for server_id = 1
    test_server_id = 1
    axes = [
        "availability",
        "confidentiality",
        "integrity",
        "privacy",
        "reliability",
        "scalability",
        "usability",
    ]
    now = datetime.utcnow()
    with TestSessionLocal() as db:
        for i, axis in enumerate(axes):
            score = McpLlmAxisScore(
                server_id=test_server_id,
                axis_name=axis,
                probs={"dummy": i / 10.0},
                p_top=0.5,
                p_critical=0.2,
                p_danger=0.1,
                label="low",
                scored_at=now,
                # Fill required non‑null columns with placeholder values
                adapter_sha256="a" * 64,
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=i + 1,
                label_index=0,
                model_version="model-1",
            )
            db.add(score)
        db.commit()

    # Build FastAPI app with overridden dependency
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    response = client.get(f"/api/servers/{test_server_id}/axis-evidence")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()
    assert isinstance(data, list), "Response is not a list"
    assert len(data) == 7, f"Expected 7 axis rows, got {len(data)}"

    for item in data:
        # probs must be present and be a dict / JSON‑serialisable
        assert "probs" in item, "Missing probs"
        assert isinstance(item["probs"], dict), "probs not a dict"
        # p_top must be within [0,1]
        p_top = item.get("p_top")
        assert isinstance(p_top, (int, float)), "p_top not numeric"
        assert 0.0 <= p_top <= 1.0, f"p_top out of range: {p_top}"
        # scored_at must be ISO‑8601 parsable
        scored_at = item.get("scored_at")
        try:
            datetime.fromisoformat(scored_at.replace("Z", "+00:00"))
        except Exception as exc:
            raise AssertionError(f"scored_at not ISO8601: {scored_at}") from exc

    print("PASS")