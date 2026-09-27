# deps: fastapi, pydantic, sqlalchemy
from datetime import datetime, timedelta
from collections import defaultdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["sprint_progress"])


class DayProgress(BaseModel):
    date: str
    scored_today: int
    cumulative_servers: int
    new_servers: int


class SprintProgressResponse(BaseModel):
    sprint_start: str
    sprint_end: str
    days: list[DayProgress]


def compute_sprint_progress(session: Session) -> SprintProgressResponse:
    today = datetime.now().date()
    sprint_start = today - timedelta(days=13)
    sprint_end = today

    scores = session.query(
        func.date(McpLlmAxisScore.scored_at).label("date"),
        McpLlmAxisScore.server_id,
    ).filter(
        func.date(McpLlmAxisScore.scored_at) >= sprint_start,
        func.date(McpLlmAxisScore.scored_at) <= sprint_end,
    ).all()

    daily_scores = defaultdict(lambda: {"count": 0, "servers": set()})
    for row in scores:
        daily_scores[row.date]["count"] += 1
        daily_scores[row.date]["servers"].add(row.server_id)

    days = []
    cumulative = set()

    for i in range(14):
        d = sprint_start + timedelta(days=i)
        if d in daily_scores:
            new_servers = daily_scores[d]["servers"] - cumulative
            cumulative.update(daily_scores[d]["servers"])
            days.append(
                DayProgress(
                    date=d.isoformat(),
                    scored_today=daily_scores[d]["count"],
                    cumulative_servers=len(cumulative),
                    new_servers=len(new_servers),
                )
            )
        else:
            days.append(
                DayProgress(
                    date=d.isoformat(),
                    scored_today=0,
                    cumulative_servers=len(cumulative),
                    new_servers=0,
                )
            )

    return SprintProgressResponse(
        sprint_start=sprint_start.isoformat(),
        sprint_end=sprint_end.isoformat(),
        days=days,
    )


@router.get("/sprint/progress", response_model=SprintProgressResponse)
def get_sprint_progress(session: Session = Depends(get_session)):
    return compute_sprint_progress(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    McpLlmAxisScore.__table__.create_all(engine, checkfirst=True)

    session = TestingSessionLocal()

    today = datetime.now().date()
    d1 = today - timedelta(days=2)
    d2 = today - timedelta(days=1)
    d3 = today

    for i in [1, 2]:
        session.add(
            McpLlmAxisScore(
                server_id=f"srv_{i}",
                axis_name="test",
                scored_at=datetime.combine(d1, datetime.min.time()),
            )
        )
    for i in [1, 2, 3]:
        session.add(
            McpLlmAxisScore(
                server_id=f"srv_{i}",
                axis_name="test",
                scored_at=datetime.combine(d2, datetime.min.time()),
            )
        )
    for i in [3, 4, 5]:
        session.add(
            McpLlmAxisScore(
                server_id=f"srv_{i}",
                axis_name="test",
                scored_at=datetime.combine(d3, datetime.min.time()),
            )
        )
    session.commit()
    session.close()

    def override_get_session():
        try:
            yield TestingSessionLocal()
        finally:
            pass

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)
    response = client.get("/api/sprint/progress")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()

    assert len(data["days"]) >= 3, f"Expected days >= 3, got {len(data['days'])}"

    final_day = data["days"][-1]
    assert (
        final_day["cumulative_servers"] >= 1
    ), f"Expected cumulative_servers >= 1, got {final_day['cumulative_servers']}"

    print("PASS")
