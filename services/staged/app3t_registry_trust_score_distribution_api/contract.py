"""Acceptance self-test for the registry trust-score distribution service."""

from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    session = testing_session()
    try:
        verdicts = ("TRUSTED", "REVIEW", "REJECTED")
        for index in range(10):
            session.add(
                McpServerRegistry(
                    server_id=f"registry-{index}",
                    name=f"Registry Server {index}",
                    trust_score=(index + 0.5) / 10,
                    verdict=verdicts[index % len(verdicts)],
                    risk_tier="MEDIUM",
                    registry_source="acceptance",
                    last_assessed=None,
                )
            )
        session.add(
            McpServerRegistry(
                server_id="registry-null-1",
                name="Registry Server Without Score 1",
                trust_score=None,
                verdict="REVIEW",
                risk_tier="HIGH",
                registry_source="acceptance",
                last_assessed=None,
            )
        )
        session.add(
            McpServerRegistry(
                server_id="registry-null-2",
                name="Registry Server Without Score 2",
                trust_score=None,
                verdict="UNKNOWN",
                risk_tier="LOW",
                registry_source="acceptance",
                last_assessed=None,
            )
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        response = client.get("/api/registry/trust-score-distribution")
        assert response.status_code == 200, response.text
        body = response.json()

    assert body["total"] == 12, body
    assert body["null_count"] == 2, body
    assert sum(bucket["count"] for bucket in body["bins"]) == 10, body
    assert len(body["bins"]) == 10, body
    assert [bucket["bin"] for bucket in body["bins"]] == list(range(10)), body
    assert all(bucket["count"] == 1 for bucket in body["bins"]), body

    verdict_totals: dict[str, int] = {}
    for bucket in body["bins"]:
        for verdict, count in bucket["verdicts"].items():
            verdict_totals[verdict] = verdict_totals.get(verdict, 0) + count
    assert verdict_totals == {"TRUSTED": 4, "REVIEW": 3, "REJECTED": 3}, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)