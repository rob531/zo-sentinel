# services/staged/verdict_view/contract.py
from fastapi import FastAPI, APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from typing import Dict

# Real data layer imports (must remain unchanged for production)
from app.db import get_session
from app.models import ServerRegistry, LlmAxisScore, Base

router = APIRouter()


class AxisScoreModel(Dict):
    """Placeholder for type hinting; actual schema is defined via Pydantic below."""
    pass


class VerdictResponseModel:
    """Pydantic model for the API response."""
    from pydantic import BaseModel

    class AxisScore(BaseModel):
        label: str
        p_top: float

    class Response(BaseModel):
        server_id: int
        verdict: str
        risk_tier: str
        scores: Dict[str, AxisScore]


@router.get(
    "/api/verdict/{server_id}",
    response_model=VerdictResponseModel.Response,
    tags=["verdict_view"],
)
def get_verdict_view(
    server_id: int, db: Session = Depends(get_session)
) -> VerdictResponseModel.Response:
    """Return verdict information for a given server."""
    server = db.query(ServerRegistry).filter(ServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    scores_q = (
        db.query(LlmAxisScore)
        .filter(LlmAxisScore.server_id == server_id)
        .all()
    )
    scores: Dict[str, VerdictResponseModel.AxisScore] = {}
    for s in scores_q:
        scores[s.axis] = VerdictResponseModel.AxisScore(label=s.label, p_top=s.p_top)

    return VerdictResponseModel.Response(
        server_id=server.server_id,
        verdict=server.verdict,
        risk_tier=server.risk_tier,
        scores=scores,
    )


app = FastAPI()
app.include_router(router)


# ----------------------------------------------------------------------
# Self‑test (run with: python -m services.staged.verdict_view.contract)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite DB with a StaticPool (shared across threads)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Seed test data
    db: Session = SessionLocal()
    test_server = ServerRegistry(server_id=1, verdict="allow", risk_tier="low")
    test_score = LlmAxisScore(
        server_id=1, axis="security", label="high", p_top=0.95
    )
    db.add_all([test_server, test_score])
    db.commit()

    # Override the dependency to use the in‑memory session
    def get_test_session() -> Session:
        return db

    app.dependency_overrides[get_session] = get_test_session

    # Run test client
    client = TestClient(app)
    resp = client.get("/api/verdict/1")
    try:
        assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
        data = resp.json()
        assert data["server_id"] == 1
        assert data["verdict"] == "allow"
        assert data["risk_tier"] == "low"
        assert "security" in data["scores"]
        assert data["scores"]["security"]["label"] == "high"
        assert abs(data["scores"]["security"]["p_top"] - 0.95) < 1e-6
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)