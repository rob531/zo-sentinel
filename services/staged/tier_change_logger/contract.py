from __future__ import annotations

import sys
from datetime import datetime, timedelta


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpLlmAxisScore, McpServerRegistry, PerspectiveEvent

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    first_scored_at = datetime.now() - timedelta(days=2)
    latest_scored_at = datetime.now() - timedelta(days=1)
    servers = (
        ("1", "Server Alpha", 0.20, 0.75),
        ("2", "Server Beta", 0.20, 0.25),
        ("3", "Server Gamma", 0.85, 0.95),
    )

    with TestingSession() as session:
        session.add_all(
            McpServerRegistry(server_id=server_id, name=name)
            for server_id, name, _, _ in servers
        )
        score_id = 1
        for server_id, _, first_p_top, latest_p_top in servers:
            for period, p_top, scored_at in (
                ("first", first_p_top, first_scored_at),
                ("latest", latest_p_top, latest_scored_at),
            ):
                session.add(
                    McpLlmAxisScore(
                        id=score_id,
                        server_id=server_id,
                        axis_name="overall_risk",
                        p_top=p_top,
                        model_version=f"contract-{server_id}-{period}",
                        scored_at=scored_at,
                    )
                )
                score_id += 1
        session.commit()

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_session] = override_get_session

    response = TestClient(app).get("/api/scoring/tier-change-log?days=7")
    assert response.status_code == 200, response.text
    body = response.json()
    changes = body["changes"]
    assert len(changes) == 1, body

    change = changes[0]
    assert change["server_id"] == 1, change
    assert change["server_name"] == "Server Alpha", change
    assert change["old_tier"] == "LOW", change
    assert change["new_tier"] == "HIGH", change
    assert datetime.fromisoformat(change["changed_at"]) == latest_scored_at, change

    with TestingSession() as session:
        events = (
            session.query(PerspectiveEvent)
            .filter_by(
                perspective_id="tier_change_logger",
                server_id="1",
                change_type="tier_change",
                old_tier="LOW",
                new_tier="HIGH",
            )
            .all()
        )
        assert len(events) == 1, events

    engine.dispose()
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)