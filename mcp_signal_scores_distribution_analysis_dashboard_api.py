from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Dict
from app.db import get_session
from app.models import MCP_Signal_Scores

router = APIRouter()

class SignalScoresDistribution(BaseModel):
    signal: Dict[str, Dict[float, int]]

@router.get("/signal-scores-distribution", response_model=SignalScoresDistribution)
async def get_signal_scores_distribution(db: Session = Depends(get_session)):
    signal_scores = db.query(MCP_Signal_Scores.signal, MCP_Signal_Scores.score).all()

    distribution = {}
    for signal, score in signal_scores:
        if signal not in distribution:
            distribution[signal] = {}
        if score not in distribution[signal]:
            distribution[signal][score] = 0
        distribution[signal][score] += 1

    return {"signal": distribution}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCP_Signal_Scores
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory database for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    # Seed test data
    test_data = [
        MCP_Signal_Scores(signal="signal1", score=0.1),
        MCP_Signal_Scores(signal="signal1", score=0.2),
        MCP_Signal_Scores(signal="signal1", score=0.1),
        MCP_Signal_Scores(signal="signal2", score=0.3),
        MCP_Signal_Scores(signal="signal2", score=0.3),
        MCP_Signal_Scores(signal="signal2", score=0.3),
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Override dependency for testing
    from app import dependency_overrides
    dependency_overrides[get_session] = lambda: test_session

    # Create test client
    from app.main import app
    client = TestClient(app)

    # Test endpoint
    response = client.get("/signal-scores-distribution")
    assert response.status_code == 200
    assert response.json() == {
        "signal": {
            "signal1": {0.1: 2, 0.2: 1},
            "signal2": {0.3: 3}
        }
    }

    print("PASS")