from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.db import get_session
from app.models import McpScoreRun

router = APIRouter()

class ScoreRunCreate(BaseModel):
    run_id: str
    start_time: str
    end_time: str
    server_count: int

@router.post("/scores/run", response_model=ScoreRunCreate, status_code=status.HTTP_201_CREATED)
async def create_score_run(score_run: ScoreRunCreate, session=Depends(get_session)):
    try:
        query = text("""
            INSERT INTO mcp_score_runs (run_id, start_time, end_time, server_count)
            VALUES (:run_id, :start_time, :end_time, :server_count)
            ON CONFLICT (run_id) DO NOTHING
            RETURNING run_id, start_time, end_time, server_count
        """)
        result = session.execute(
            query,
            {
                "run_id": score_run.run_id,
                "start_time": score_run.start_time,
                "end_time": score_run.end_time,
                "server_count": score_run.server_count,
            }
        ).fetchone()

        if not result:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Score run with this ID already exists"
            )

        return ScoreRunCreate(**dict(result))
    except SQLAlchemyError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the database session for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    client = TestClient(app)

    test_data = {
        "run_id": "test_run_1",
        "start_time": "2023-01-01T00:00:00Z",
        "end_time": "2023-01-01T01:00:00Z",
        "server_count": 5
    }

    response = client.post("/scores/run", json=test_data)
    assert response.status_code == 201
    assert response.json() == test_data

    print("PASS")