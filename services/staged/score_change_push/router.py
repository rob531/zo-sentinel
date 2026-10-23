from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import ScorePushResponse, push_scores as query_score_changes

router = APIRouter(prefix="/api", tags=["score_change_push"])


@router.post("/scores/push", response_model=ScorePushResponse)
async def push_scores(
    since: datetime = Query(..., description="ISO-8601 timestamp"),
    db: Session = Depends(get_session),
) -> ScorePushResponse:
    return await query_score_changes(since=since, db=db)


if __name__ == "__main__":
    from datetime import timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app.models import McpLlmAxisScore, McpServerRegistry

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestingSession() as db:
        db.add_all(
            [
                McpServerRegistry(server_id=str(index), name=f"Server{index}")
                for index in range(1, 4)
            ]
        )
        db.add_all(
            [
                McpLlmAxisScore(
                    id=index,
                    server_id=str(index),
                    axis_name=f"axis{index}",
                    p_top=0.5 + index / 10,
                    p_critical=0.1,
                    model_version="self-test",
                    scored_at=now - timedelta(minutes=4 - index),
                )
                for index in range(1, 4)
            ]
        )
        db.commit()

    def override_get_session():
        with TestingSession() as db:
            yield db

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    since = (now - timedelta(minutes=10)).isoformat()
    response = TestClient(that_app).post(f"/api/scores/push?since={since}")
    assert response.status_code == 200, response.text
    assert len(response.json()["events"]) >= 3, response.text
    print("PASS")