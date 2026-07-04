from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db import get_session
from app.models import McpLlmAxisScores
from typing import Dict

router = APIRouter()

@router.get("/analysis/llm-axis-scores-distribution", response_model=Dict[str, int])
def get_llm_axis_scores_distribution(session: Session = Depends(get_session)) -> Dict[str, int]:
    distribution = (
        session.query(
            McpLlmAxisScores.axis_name,
            func.count(McpLlmAxisScores.axis_name).label("count")
        )
        .group_by(McpLlmAxisScores.axis_name)
        .order_by(func.count(McpLlmAxisScores.axis_name).desc())
        .limit(5)
        .all()
    )
    return {axis: count for axis, count in distribution}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    import pytest

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = APIRouter()
    app.include_router(router)

    client = TestClient(app)

    def test_get_llm_axis_scores_distribution():
        db = TestingSessionLocal()
        db.add_all([
            McpLlmAxisScores(axis_name="axis1"),
            McpLlmAxisScores(axis_name="axis1"),
            McpLlmAxisScores(axis_name="axis2"),
            McpLlmAxisScores(axis_name="axis2"),
            McpLlmAxisScores(axis_name="axis2"),
            McpLlmAxisScores(axis_name="axis3"),
            McpLlmAxisScores(axis_name="axis3"),
            McpLlmAxisScores(axis_name="axis3"),
            McpLlmAxisScores(axis_name="axis3"),
            McpLlmAxisScores(axis_name="axis4"),
            McpLlmAxisScores(axis_name="axis4"),
            McpLlmAxisScores(axis_name="axis4"),
            McpLlmAxisScores(axis_name="axis4"),
            McpLlmAxisScores(axis_name="axis4"),
            McpLlmAxisScores(axis_name="axis5"),
            McpLlmAxisScores(axis_name="axis5"),
            McpLlmAxisScores(axis_name="axis5"),
            McpLlmAxisScores(axis_name="axis5"),
            McpLlmAxisScores(axis_name="axis5"),
            McpLlmAxisScores(axis_name="axis6"),
        ])
        db.commit()
        db.close()

        response = client.get("/analysis/llm-axis-scores-distribution")
        assert response.status_code == 200
        assert response.json() == {
            "axis5": 5,
            "axis4": 4,
            "axis3": 4,
            "axis2": 3,
            "axis1": 2,
        }

    test_get_llm_axis_scores_distribution()
    print("PASS")