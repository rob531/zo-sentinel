from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Dict, Optional
from sqlalchemy import select, func
from sqlalchemy.orm import Session

router = APIRouter()

class SignalScoresDistribution(BaseModel):
    signal: str
    scores: Dict[float, int]

class SignalScoresResponse(BaseModel):
    data: Dict[str, SignalScoresDistribution]
    override_tier: Optional[str] = None

def get_db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    engine = create_engine("sqlite:///:memory:", echo=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()

@router.get("/signal-scores-distribution", response_model=SignalScoresResponse)
def get_signal_scores_distribution(db: Session = Depends(get_db_session)):
    from app.models import MCP_Signal_Scores

    # Query to get the distribution of signal scores
    query = (
        select(
            MCP_Signal_Scores.signal,
            MCP_Signal_Scores.score,
            func.count(MCP_Signal_Scores.score).label("count")
        )
        .group_by(MCP_Signal_Scores.signal, MCP_Signal_Scores.score)
    )

    result = db.execute(query)
    rows = result.fetchall()

    # Build the response data
    data = {}
    for row in rows:
        signal, score, count = row
        if signal not in data:
            data[signal] = {"scores": {}}
        data[signal]["scores"][score] = count

    # Check for CRITICAL axis override
    override_tier = None
    critical_query = select(MCP_Signal_Scores.tier).where(MCP_Signal_Scores.axis == "CRITICAL").limit(1)
    critical_result = db.execute(critical_query)
    critical_row = critical_result.fetchone()
    if critical_row:
        override_tier = critical_row[0]

    return {"data": data, "override_tier": override_tier}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.models import MCP_Signal_Scores, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory database and seed data
    engine = create_engine("sqlite:///:memory:", echo=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = SessionLocal()
    signals = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]
    for signal in signals:
        for score in [0.1, 0.2, 0.3, 0.4, 0.5]:
            db.add(MCP_Signal_Scores(signal=signal, score=score, axis="NORMAL", tier="LOW"))
    # Add a CRITICAL axis to test override
    db.add(MCP_Signal_Scores(signal="S1", score=0.5, axis="CRITICAL", tier="HIGH"))
    db.commit()

    # Create FastAPI app and test client
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/signal-scores-distribution")
    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]) == 8
    assert data["override_tier"] == "HIGH"
    print("PASS")