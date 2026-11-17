from collections.abc import Generator
from datetime import date, datetime, time, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute

from .router import router as api_router


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    McpScoreDispute.__table__.create(engine, checkfirst=True)

    now = datetime.now()
    today = date.today()

    with testing_session() as session:
        session.add_all(
            [
                McpScoreDispute(
                    id=1,
                    server_id="server-open-2d",
                    submitted_by="reviewer@example.test",
                    proposed_overall_risk=0.25,
                    explanation="Recently submitted score dispute.",
                    reason_category="risk_score",
                    status="OPEN",
                    created_at=datetime.combine(today - timedelta(days=2), time.min),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    id=2,
                    server_id="server-open-60d",
                    submitted_by="reviewer@example.test",
                    proposed_overall_risk=0.50,
                    explanation="Dispute awaiting review.",
                    reason_category="risk_score",
                    status="IN_REVIEW",
                    created_at=datetime.combine(today - timedelta(days=60), time.min),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    id=3,
                    server_id="server-resolved",
                    submitted_by="reviewer@example.test",
                    proposed_overall_risk=0.75,
                    explanation="Resolved score dispute.",
                    reason_category="evidence",
                    status="RESOLVED",
                    created_at=datetime.combine(today - timedelta(days=30), time.min),
                    resolved_at=now,
                ),
                McpScoreDispute(
                    id=4,
                    server_id="server-open-200d",
                    submitted_by="reviewer@example.test",
                    proposed_overall_risk=0.90,
                    explanation="Long-running unresolved dispute.",
                    reason_category="policy",
                    status="OPEN",
                    created_at=datetime.combine(today - timedelta(days=200), time.min),
                    resolved_at=None,
                ),
            ]
        )
        session.commit()

    def override_get_session() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    router_prefix = getattr(api_router, "prefix", "") or ""
    route_paths = [getattr(route, "path", "") for route in api_router.routes]
    if router_prefix.startswith("/api") or any(
        path.startswith("/api/") for path in route_paths
    ):
        test_app.include_router(api_router)
    else:
        test_app.include_router(api_router, prefix="/api")

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/backlog-age")
        response.raise_for_status()
        payload = response.json()

    assert payload["total_open"] == 3, payload
    buckets = {item["bucket"]: item["count"] for item in payload["buckets"]}
    assert buckets["90d+"] == 1, payload
    assert payload["oldest_open_days"] >= 200, payload

    engine.dispose()


def main() -> None:
    run_self_test()
    print("PASS")


if __name__ == "__main__":
    main()