# deps: fastapi; pydantic; sqlalchemy; sqlmodel; passlib
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from datetime import datetime

router = APIRouter(prefix="/api/signal-scores", tags=["Signal Scores"])

class SignalScoreResponse(BaseModel):
    server_id: int
    server_name: str
    axis_name: str
    label: str
    probs: dict
    p_top: float
    p_critical: float
    p_danger: float
    scored_at: datetime
    model_version: str

class SignalScoresRequest(BaseModel):
    server_ids: Optional[List[int]] = None
    risk_tiers: Optional[List[str]] = None
    min_score: Optional[float] = None

@router.get(
            "/",
            response_model=List[SignalScoreResponse],
            summary="Get signal scores for servers"
        )
async def get_signal_scores(
    request: SignalScoresRequest = Depends(),
    db: Session = Depends(get_session)
):
    try:
        query = db.query(McpLlmAxisScore).join(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id
        )

        if request.server_ids:
            query = query.filter(McpLlmAxisScore.server_id.in_(request.server_ids))
        if request.risk_tiers:
            query = query.join(
                McpServerRegistry,
                McpLlmAxisScore.server_id == McpServerRegistry.server_id
            ).filter(McpServerRegistry.risk_tier.in_(request.risk_tiers))
        if request.min_score:
            query = query.filter(McpLlmAxisScore.p_critical >= request.min_score)

        results = query.all()

        return [{
            "server_id": score.server_id,
            "server_name": score.mcp_server_registry.name,
            "axis_name": score.axis_name,
            "label": score.label,
            "probs": score.probs,
            "p_top": score.p_top,
            "p_critical": score.p_critical,
            "p_danger": score.p_danger,
            "scored_at": score.scored_at,
            "model_version": score.model_version
        } for score in results]

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching signal scores: {str(e)}"
        )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create test app with in-memory SQLite
    test_app = FastAPI()
    test_app.include_router(router)

    # Override get_session for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    # Create test client
    client = TestClient(test_app)

    # Test data setup
    test_server = McpServerRegistry(
        server_id=1,
        name="Test Server",
        risk_tier="medium"
    )

    test_score = McpLlmAxisScore(
        server_id=1,
        axis_name="overall_risk",
        label="medium",
        probs={"low": 0.1, "medium": 0.6, "high": 0.2, "critical": 0.1},
        p_top=0.6,
        p_critical=0.1,
        p_danger=0.3,
        model_version="v1.0",
        scored_at=datetime.now()
    )

    # Test cases
    try:
        with test_engine.begin() as conn:
            conn.execute(McpServerRegistry.__table__.insert(), [test_server.__dict__])
            conn.execute(McpLlmAxisScore.__table__.insert(), [test_score.__dict__])

        # Test 1: Get all scores
        response = client.get("/api/signal-scores/")
        assert response.status_code == 200
        assert len(response.json()) == 1

        # Test 2: Filter by server_id
        response = client.get("/api/signal-scores/?server_ids=1")
        assert response.status_code == 200
        assert len(response.json()) == 1

        # Test 3: Filter by risk_tier
        response = client.get("/api/signal-scores/?risk_tiers=medium")
        assert response.status_code == 200
        assert len(response.json()) == 1

        print("PASS")
    except Exception as e:
        print(f"FAIL: {str(e)}")