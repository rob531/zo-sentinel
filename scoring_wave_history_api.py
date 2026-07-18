from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List
from datetime import datetime
from app.db import get_session
from app.models import McpLlmAxisScores

router = APIRouter()

class ScoringWave(BaseModel):
    scored_at: datetime
    server_count: int
    axes_covered: int
    unique_adapters: int
    avg_p_top: float

class ScoringWaveHistoryResponse(BaseModel):
    waves: List[ScoringWave]

@router.get("/scoring/waves/history", response_model=ScoringWaveHistoryResponse)
def get_scoring_wave_history(db: Session = Depends(get_session)):
    # Query distinct scored_at batches with counts and aggregates
    results = db.query(
        McpLlmAxisScores.scored_at,
        func.count(distinct=McpLlmAxisScores.server_id).label("server_count"),
        func.count(distinct=McpLlmAxisScores.axis_id).label("axes_covered"),
        func.count(distinct=McpLlmAxisScores.adapter_id).label("unique_adapters"),
        func.avg(McpLlmAxisScores.p_top).label("avg_p_top")
    ).group_by(
        McpLlmAxisScores.scored_at
    ).order_by(
        McpLlmAxisScores.scored_at.desc()
    ).limit(50).all()

    # Convert to response model
    waves = [
        ScoringWave(
            scored_at=row.scored_at,
            server_count=row.server_count,
            axes_covered=row.axes_covered,
            unique_adapters=row.unique_adapters,
            avg_p_top=row.avg_p_top
        )
        for row in results
    ]

    return ScoringWaveHistoryResponse(waves=waves)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, func
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test app
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)

    # Add test data
    with TestSession() as session:
        from datetime import datetime, timedelta
        now = datetime.now()
        for i in range(1, 6):
            scored_at = now - timedelta(days=i)
            for j in range(1, 4):
                session.execute(
                    McpLlmAxisScores.__table__.insert().values(
                        scored_at=scored_at,
                        server_id=j,
                        axis_id=j,
                        adapter_id=j,
                        p_top=0.5 + (i * 0.1)
                    )
                )
        session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/scoring/waves/history")
    assert response.status_code == 200
    data = response.json()
    assert "waves" in data
    assert len(data["waves"]) >= 1
    assert all(field in data["waves"][0] for field in [
        "scored_at", "server_count", "axes_covered",
        "unique_adapters", "avg_p_top"
    ])
    print("PASS")