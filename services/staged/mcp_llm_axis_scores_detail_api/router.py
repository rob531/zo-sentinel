"""Router for the MCP LLM Axis Scores Detail API."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Real application dependencies
from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# Business logic
from .logic import AxisScoresDetailResponse, get_axis_scores_detail

router = APIRouter(prefix="/api")


@router.get(
    "/servers/{server_id}/axis-scores",
    response_model=AxisScoresDetailResponse,
    tags=["axis-scores"],
)
def axis_scores_detail(
    server_id: int,
    session: Session = Depends(get_session),
) -> AxisScoresDetailResponse:
    """Return detailed LLM axis scores for a given server."""
    return get_axis_scores_detail(session, server_id)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mimics the real models
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # ------------------------------------------------------------------- #
    # Helper to provide a session for the FastAPI dependency override
    # ------------------------------------------------------------------- #
    def get_test_session() -> Session:  # pragma: no cover
        return TestSession()

    # ------------------------------------------------------------------- #
    # Seed test data: 2 servers, each with 3 score timestamps and 7 axes
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    axis_names = [
        "confidentiality",
        "integrity",
        "availability",
        "authenticity",
        "non_repudiation",
        "privacy",
        "compliance",
    ]

    with TestSession() as s:
        # Servers
        s.add_all(
            [
                McpServerRegistry(
                    server_id=1,
                    name="alpha.example.com",
                    risk_tier="high",
                    confidence=1.0,
                    description="",
                    first_seen=now - timedelta(days=30),
                    last_assessed=now,
                    last_scanned=now,
                    last_seen=now,
                    meta={},
                    registry_source="test",
                    scan_count=0,
                    trust_score=0.0,
                    url="https://alpha.example.com",
                    verdict="",
                    verdict_reasoning="",
                ),
                McpServerRegistry(
                    server_id=2,
                    name="beta.example.com",
                    risk_tier="medium",
                    confidence=1.0,
                    description="",
                    first_seen=now - timedelta(days=30),
                    last_assessed=now,
                    last_scanned=now,
                    last_seen=now,
                    meta={},
                    registry_source="test",
                    scan_count=0,
                    trust_score=0.0,
                    url="https://beta.example.com",
                    verdict="",
                    verdict_reasoning="",
                ),
            ]
        )
        s.flush()  # obtain PKs if needed (they are server_id here)

        # Scores
        for server_id in (1, 2):
            for i in range(3):
                scored_at = now - timedelta(minutes=5 * i)
                for idx, axis in enumerate(axis_names):
                    s.add(
                        McpLlmAxisScore(
                            adapter_sha256="dummysha",
                            axis_name=axis,
                            decision_rule_version="v1",
                            escalated=False,
                            escalated_to=None,
                            id=None,
                            label=f"label-{axis}",
                            label_index=idx,
                            model_version="model-1",
                            p_critical=0.1,
                            p_danger=0.2,
                            # Known value for verification on the latest timestamp of server 1
                            p_top=0.99 if server_id == 1 and i == 0 and axis == "confidentiality" else 0.5,
                            probs="{}",
                            scored_at=scored_at,
                            server_id=server_id,
                        )
                    )
        s.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with the router and override the session dependency
    # ------------------------------------------------------------------- #
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform the request and validate the contract
    # ------------------------------------------------------------------- #
    resp = client.get("/api/servers/1/axis-scores")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "axes" in data, "Missing 'axes' in response"
    assert len(data["axes"]) >= 7, f"Expected at least 7 axes, got {len(data['axes'])}"
    # Find the axis with the known p_top value
    top_axis = next(
        (a for a in data["axes"] if a["axis_name"] == "confidentiality"), None
    )
    assert top_axis is not None, "Confidentiality axis missing"
    assert abs(top_axis["p_top"] - 0.99) < 1e-6, f"p_top mismatch: {top_axis['p_top']}"
    print("PASS")