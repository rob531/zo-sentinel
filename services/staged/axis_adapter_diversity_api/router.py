# services/staged/axis_adapter_diversity_api/router.py
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_adapter_diversity_stats

router = APIRouter(prefix="/api")


class AdapterInfo(BaseModel):
    adapter_sha256: str
    rows: int
    servers: int
    model_versions: int
    axes: int
    first_scored_at: datetime
    last_scored_at: datetime


class AdapterDiversityResponse(BaseModel):
    generated_at: datetime
    adapters: List[AdapterInfo]
    adapter_sha256_missing: int
    distinct_adapters: int


@router.get(
    "/axes/adapter-diversity",
    response_model=AdapterDiversityResponse,
    name="axis_adapter_diversity",
)
def axis_adapter_diversity(session: Session = Depends(get_session)):
    """Thin wrapper that delegates to the logic layer."""
    return get_adapter_diversity_stats(session)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import uvicorn
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpLlmAxisScore

    # ------------------------------------------------------------------- #
    # Build a minimal FastAPI app that includes this router
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and override the session dependency
    # ------------------------------------------------------------------- #
    ENGINE = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=ENGINE)

    # Create tables for the models we need
    McpLlmAxisScore.__table__.metadata.create_all(ENGINE)

    def _override_get_session() -> Session:  # type: ignore
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _override_get_session

    # ------------------------------------------------------------------- #
    # Populate the test data
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    test_rows = [
        # Adapter A (3 rows)
        McpLlmAxisScore(
            id=1,
            server_id="srv1",
            axis_name="axis1",
            label="low",
            label_index=0,
            probs="{}",
            p_top=0.7,
            p_critical=0.1,
            p_danger=0.2,
            escalated=False,
            escalated_to=None,
            decision_rule_version="v1",
            model_version="m1",
            adapter_sha256="adapterA",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=2,
            server_id="srv2",
            axis_name="axis2",
            label="high",
            label_index=1,
            probs="{}",
            p_top=0.6,
            p_critical=0.2,
            p_danger=0.2,
            escalated=True,
            escalated_to="team1",
            decision_rule_version="v1",
            model_version="m2",
            adapter_sha256="adapterA",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=3,
            server_id="srv1",
            axis_name="axis3",
            label="medium",
            label_index=2,
            probs="{}",
            p_top=0.5,
            p_critical=0.3,
            p_danger=0.2,
            escalated=False,
            escalated_to=None,
            decision_rule_version="v2",
            model_version="m1",
            adapter_sha256="adapterA",
            scored_at=now,
        ),
        # Adapter B (3 rows)
        McpLlmAxisScore(
            id=4,
            server_id="srv3",
            axis_name="axis1",
            label="low",
            label_index=0,
            probs="{}",
            p_top=0.8,
            p_critical=0.1,
            p_danger=0.1,
            escalated=False,
            escalated_to=None,
            decision_rule_version="v1",
            model_version="m1",
            adapter_sha256="adapterB",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=5,
            server_id="srv4",
            axis_name="axis2",
            label="high",
            label_index=1,
            probs="{}",
            p_top=0.6,
            p_critical=0.2,
            p_danger=0.2,
            escalated=True,
            escalated_to="team2",
            decision_rule_version="v1",
            model_version="m3",
            adapter_sha256="adapterB",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=6,
            server_id="srv3",
            axis_name="axis3",
            label="medium",
            label_index=2,
            probs="{}",
            p_top=0.55,
            p_critical=0.25,
            p_danger=0.2,
            escalated=False,
            escalated_to=None,
            decision_rule_version="v2",
            model_version="m2",
            adapter_sha256="adapterB",
            scored_at=now,
        ),
        # Adapter C (3 rows)
        McpLlmAxisScore(
            id=7,
            server_id="srv5",
            axis_name="axis1",
            label="low",
            label_index=0,
            probs="{}",
            p_top=0.9,
            p_critical=0.05,
            p_danger=0.05,
            escalated=False,
            escalated_to=None,
            decision_rule_version="v1",
            model_version="m1",
            adapter_sha256="adapterC",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=8,
            server_id="srv6",
            axis_name="axis2",
            label="high",
            label_index=1,
            probs="{}",
            p_top=0.65,
            p_critical=0.15,
            p_danger=0.2,
            escalated=True,
            escalated_to="team3",
            decision_rule_version="v1",
            model_version="m4",
            adapter_sha256="adapterC",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=9,
            server_id="srv5",
            axis_name="axis3",
            label="medium",
            label_index=2,
            probs="{}",
            p_top=0.5,
            p_critical=0.3,
            p_danger=0.2,
            escalated=False,
            escalated_to=None,
            decision_rule_version="v2",
            model_version="m3",
            adapter_sha256="adapterC",
            scored_at=now,
        ),
        # Row with missing adapter_sha256
        McpLlmAxisScore(
            id=10,
            server_id="srv7",
            axis_name="axis1",
            label="low",
            label_index=0,
            probs="{}",
            p_top=0.7,
            p_critical=0.1,
            p_danger=0.2,
            escalated=False,
            escalated_to=None,
            decision_rule_version="v1",
            model_version="m5",
            adapter_sha256=None,
            scored_at=now,
        ),
    ]

    with SessionLocal() as db:
        db.add_all(test_rows)
        db.commit()

    # ------------------------------------------------------------------- #
    # Run the test client against the endpoint
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    response = client.get("/api/axes/adapter-diversity")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()
    assert payload["distinct_adapters"] == 3, "distinct_adapters mismatch"
    assert payload["adapter_sha256_missing"] == 1, "adapter_sha256_missing mismatch"
    print("PASS")