from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpSignalScore

router = APIRouter(prefix="/api", tags=["signal_scores"])

@router.get("/signal-scores/distribution")
def get_signal_scores_distribution(db: Session = Depends(get_session)) -> dict:
    """Get the distribution of signal scores across all servers."""
    # Query the database for signal scores distribution
    results = db.query(McpSignalScore.signal, McpSignalScore.score, func.count(McpSignalScore.score)).group_by(McpSignalScore.signal, McpSignalScore.score).all()
    
    # Format the results into a dictionary
    distribution = {}
    for signal, score, count in results:
        if signal not in distribution:
            distribution[signal] = {}
        distribution[signal][score] = count
    
    return distribution

if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, func
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # Set up an in-memory SQLite database for testing
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Seed the database with test data
    db = SessionLocal()
    db.add(McpSignalScore(signal="signal1", score=1))
    db.add(McpSignalScore(signal="signal1", score=2))
    db.add(McpSignalScore(signal="signal2", score=1))
    db.commit()
    db.close()

    # Set up the FastAPI app and test client
    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/api/signal-scores/distribution")
    assert response.status_code == 200
    data = response.json()
    assert "signal1" in data
    assert "signal2" in data
    assert data["signal1"][1] == 1
    assert data["signal1"][2] == 1
    assert data["signal2"][1] == 1

    print("PASS")