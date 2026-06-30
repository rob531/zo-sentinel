from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Dict
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .database import get_db
from .models import MCP_Signal_Scores

router = APIRouter()

class SignalDistribution(BaseModel):
    signal_type: str
    count: int
    min_score: float
    max_score: float
    avg_score: float

@router.get("/signals/distribution", response_model=List[SignalDistribution])
def get_signal_distribution(db: Session = Depends(get_db)):
    # Query to get distribution of signal scores by signal type
    query = (
        select(
            MCP_Signal_Scores.signal_type,
            func.count(MCP_Signal_Scores.id).label("count"),
            func.min(MCP_Signal_Scores.score).label("min_score"),
            func.max(MCP_Signal_Scores.score).label("max_score"),
            func.avg(MCP_Signal_Scores.score).label("avg_score")
        )
        .group_by(MCP_Signal_Scores.signal_type)
    )

    results = db.execute(query).fetchall()

    # Convert results to list of SignalDistribution
    distribution = [
        SignalDistribution(
            signal_type=row.signal_type,
            count=row.count,
            min_score=row.min_score,
            max_score=row.max_score,
            avg_score=row.avg_score
        )
        for row in results
    ]

    return distribution

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from .database import Base, engine
    from .models import MCP_Signal_Scores

    # Create in-memory database and seed with test data
    Base.metadata.create_all(bind=engine)
    test_app = FastAPI()
    test_app.include_router(router)

    # Seed test data
    with Session(engine) as session:
        test_data = [
            MCP_Signal_Scores(signal_type="type1", score=0.5),
            MCP_Signal_Scores(signal_type="type1", score=0.7),
            MCP_Signal_Scores(signal_type="type2", score=0.3),
            MCP_Signal_Scores(signal_type="type2", score=0.9),
            MCP_Signal_Scores(signal_type="type2", score=0.6),
        ]
        session.add_all(test_data)
        session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/signals/distribution")
    assert response.status_code == 200

    # Expected distribution based on test data
    expected = [
        {"signal_type": "type1", "count": 2, "min_score": 0.5, "max_score": 0.7, "avg_score": 0.6},
        {"signal_type": "type2", "count": 3, "min_score": 0.3, "max_score": 0.9, "avg_score": 0.6},
    ]

    # Sort results for comparison
    actual = sorted(response.json(), key=lambda x: x["signal_type"])
    expected_sorted = sorted(expected, key=lambda x: x["signal_type"])

    assert actual == expected_sorted
    print("PASS")