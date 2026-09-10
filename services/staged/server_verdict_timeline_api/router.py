from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_server_verdict_timeline, ServerVerdictTimelineResponse

router = APIRouter(prefix="/api")


@router.get(
    "/servers/{server_id}/verdict/timeline",
    response_model=ServerVerdictTimelineResponse,
)
def server_verdict_timeline(
    server_id: str, session: Session = Depends(get_session)
):
    return get_server_verdict_timeline(server_id, session)


if __name__ == "__main__":
    import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import (
        Base,
        McpServerRegistry,
        McpLlmAxisScore,
    )
    from app.db import get_session as app_get_session

    # In‑memory SQLite setup
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    # Seed data
    db = SessionLocal()
    srv1 = McpServerRegistry(
        server_id="srv1",
        name="Server One",
        risk_tier="low",
        verdict="ok",
    )
    srv2 = McpServerRegistry(
        server_id="srv2",
        name="Server Two",
        risk_tier="medium",
        verdict="ok",
    )
    db.add_all([srv1, srv2])
    db.flush()

    timestamps = [
        datetime.datetime(2023, 1, 1, 0, 0, 0),
        datetime.datetime(2023, 1, 2, 0, 0, 0),
        datetime.datetime(2023, 1, 3, 0, 0, 0),
    ]

    scores = []
    for ts in timestamps:
        scores.append(
            McpLlmAxisScore(
                server_id="srv1",
                axis_name="cpu",
                p_top=0.5,
                p_critical=0.1,
                scored_at=ts,
            )
        )
        scores.append(
            McpLlmAxisScore(
                server_id="srv2",
                axis_name="cpu",
                p_top=0.6,
                p_critical=0.2,
                scored_at=ts,
            )
        )
    db.add_all(scores)
    db.commit()
    db.close()

    # FastAPI app with dependency override
    app = FastAPI()
    app.include_router(router)


    def get_test_session():
        test_db = SessionLocal()
        try:
            yield test_db
        finally:
            test_db.close()


    app.dependency_overrides[app_get_session] = get_test_session

    client = TestClient(app)
    response = client.get("/api/servers/srv1/verdict/timeline")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    json_body = response.json()
    assert json_body.get("verdict_events"), "verdict_events is empty"
    print("PASS")