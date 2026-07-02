# deps: fastapi, pydantic, sqlalchemy, requests
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpSignalScore

router = APIRouter(prefix="/api", tags=["signal_scores"])

class SignalDistribution(BaseModel):
    average_score: Optional[float] = None
    median_score: Optional[float] = None
    min_score: Optional[float] = None
    max_score: Optional[float] = None
    percentile_25: Optional[float] = None
    percentile_75: Optional[float] = None

class SignalScoresDistribution(BaseModel):
    signals: Dict[str, SignalDistribution]

@router.get("/signal-scores-distribution", response_model=SignalScoresDistribution)
def get_signal_scores_distribution(db: Session = Depends(get_session)) -> SignalScoresDistribution:
    """Get the distribution of signal scores from mcp_signal_scores."""
    try:
        # Query the distribution of signal scores for each signal
        signal_names = db.execute(select(McpSignalScore.signal_name).distinct()).scalars().all()
        signal_distributions = {}

        for signal_name in signal_names:
            # Calculate the distribution statistics for the current signal
            stats = db.execute(
                select(
                    func.avg(McpSignalScore.score).label("average_score"),
                    func.percentile_cont(0.5).within_group(McpSignalScore.score).label("median_score"),
                    func.min(McpSignalScore.score).label("min_score"),
                    func.max(McpSignalScore.score).label("max_score"),
                    func.percentile_cont(0.25).within_group(McpSignalScore.score).label("percentile_25"),
                    func.percentile_cont(0.75).within_group(McpSignalScore.score).label("percentile_75")
                ).where(McpSignalScore.signal_name == signal_name)
            ).first()

            signal_distributions[signal_name] = SignalDistribution(
                average_score=stats.average_score,
                median_score=stats.median_score,
                min_score=stats.min_score,
                max_score=stats.max_score,
                percentile_25=stats.percentile_25,
                percentile_75=stats.percentile_75
            )

        return SignalScoresDistribution(signals=signal_distributions)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    
    # Seed the database with test data
    test_signals = [
        McpSignalScore(id=1, signal_name="signal1", score=10.0),
        McpSignalScore(id=2, signal_name="signal1", score=20.0),
        McpSignalScore(id=3, signal_name="signal1", score=30.0),
        McpSignalScore(id=4, signal_name="signal2", score=40.0),
        McpSignalScore(id=5, signal_name="signal2", score=50.0),
        McpSignalScore(id=6, signal_name="signal2", score=60.0),
        McpSignalScore(id=7, signal_name="signal3", score=70.0),
    ]
    s.add_all(test_signals)
    s.commit(); s.close()
    
    app = FastAPI(); app.include_router(router)
    
    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()
    
    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)
    r = c.get("/api/signal-scores-distribution"); assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["signals"]) == 3, j  # Ensure all 3 signals are returned
    assert j["signals"]["signal1"]["average_score"] == 20.0, j  # Verify average score for signal1
    assert j["signals"]["signal2"]["median_score"] == 50.0, j  # Verify median score for signal2
    assert j["signals"]["signal3"]["min_score"] == 70.0, j  # Verify min score for signal3
    assert j["signals"]["signal3"]["max_score"] == 70.0, j  # Verify max score for signal3
    assert j["signals"]["signal3"]["percentile_25"] == 70.0, j  # Verify percentile_25 score for signal3
    assert j["signals"]["signal3"]["percentile_75"] == 70.0, j  # Verify percentile_75 score for signal3
    print("PASS")