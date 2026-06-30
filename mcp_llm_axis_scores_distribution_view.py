from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Dict, List
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from .database import get_db
from .models import MCPLLMAxisScore

router = APIRouter()

class AxisScoreDistribution(BaseModel):
    score_range: str
    count: int

class AxisDistributions(BaseModel):
    axis_name: str
    distributions: List[AxisScoreDistribution]

@router.get("/llm_axis_scores_distribution", response_model=Dict[str, List[AxisScoreDistribution]])
def get_llm_axis_scores_distribution(db: Session = Depends(get_db)):
    # Get all unique axis names
    stmt = select(MCPLLMAxisScore.axis_name).distinct()
    axis_names = [row.axis_name for row in db.execute(stmt)]

    result = {}

    for axis_name in axis_names:
        # Get min and max scores for the current axis
        stmt = select(
            func.min(MCPLLMAxisScore.score).label("min_score"),
            func.max(MCPLLMAxisScore.score).label("max_score")
        ).where(MCPLLMAxisScore.axis_name == axis_name)
        min_max = db.execute(stmt).first()

        if not min_max:
            result[axis_name] = []
            continue

        min_score, max_score = min_max.min_score, min_max.max_score

        # Calculate score ranges (10 bins)
        range_size = (max_score - min_score) / 10
        distributions = []

        for i in range(10):
            lower_bound = min_score + i * range_size
            upper_bound = min_score + (i + 1) * range_size

            # Count scores in the current range
            stmt = select(func.count()).where(
                MCPLLMAxisScore.axis_name == axis_name,
                MCPLLMAxisScore.score >= lower_bound,
                MCPLLMAxisScore.score < upper_bound
            )
            count = db.execute(stmt).scalar()

            # Handle the last range to include max_score
            if i == 9:
                stmt = select(func.count()).where(
                    MCPLLMAxisScore.axis_name == axis_name,
                    MCPLLMAxisScore.score >= lower_bound,
                    MCPLLMAxisScore.score <= max_score
                )
                count = db.execute(stmt).scalar()

            distributions.append({
                "score_range": f"{lower_bound:.2f}-{upper_bound:.2f}",
                "count": count
            })

        result[axis_name] = distributions

    return result

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from .database import Base, engine
    from .models import MCPLLMAxisScore

    # Create tables
    Base.metadata.create_all(engine)

    # Seed test data
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    # Clear existing data
    db.query(MCPLLMAxisScore).delete()

    # Add test data
    test_data = [
        {"mcp_id": 1, "axis_name": "accuracy", "score": 0.1},
        {"mcp_id": 1, "axis_name": "accuracy", "score": 0.2},
        {"mcp_id": 1, "axis_name": "accuracy", "score": 0.3},
        {"mcp_id": 1, "axis_name": "accuracy", "score": 0.4},
        {"mcp_id": 1, "axis_name": "accuracy", "score": 0.5},
        {"mcp_id": 1, "axis_name": "accuracy", "score": 0.6},
        {"mcp_id": 1, "axis_name": "accuracy", "score": 0.7},
        {"mcp_id": 1, "axis_name": "accuracy", "score": 0.8},
        {"mcp_id": 1, "axis_name": "accuracy", "score": 0.9},
        {"mcp_id": 1, "axis_name": "accuracy", "score": 1.0},
        {"mcp_id": 1, "axis_name": "speed", "score": 0.1},
        {"mcp_id": 1, "axis_name": "speed", "score": 0.2},
        {"mcp_id": 1, "axis_name": "speed", "score": 0.3},
        {"mcp_id": 1, "axis_name": "speed", "score": 0.4},
        {"mcp_id": 1, "axis_name": "speed", "score": 0.5},
    ]

    for data in test_data:
        db.add(MCPLLMAxisScore(**data))

    db.commit()
    db.close()

    # Test the endpoint
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)
    response = client.get("/llm_axis_scores_distribution")

    assert response.status_code == 200
    data = response.json()

    # Verify accuracy distribution
    accuracy_dist = data["accuracy"]
    assert len(accuracy_dist) == 10
    assert accuracy_dist[0]["score_range"] == "0.10-0.20"
    assert accuracy_dist[0]["count"] == 1
    assert accuracy_dist[9]["score_range"] == "0.90-1.00"
    assert accuracy_dist[9]["count"] == 2

    # Verify speed distribution
    speed_dist = data["speed"]
    assert len(speed_dist) == 10
    assert speed_dist[0]["score_range"] == "0.10-0.20"
    assert speed_dist[0]["count"] == 1
    assert speed_dist[4]["score_range"] == "0.50-0.60"
    assert speed_dist[4]["count"] == 1

    print("PASS")