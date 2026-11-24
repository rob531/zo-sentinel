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

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    testing_session = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    session = testing_session()
    session.add_all(
        [
            McpServerRegistry(
                server_id="tier-source-a-low",
                name="Source A Low",
                registry_source="source-a",
                risk_tier="LOW",
                trust_score=90.0,
                scan_count=2,
            ),
            McpServerRegistry(
                server_id="tier-source-a-high",
                name="Source A High",
                registry_source="source-a",
                risk_tier="HIGH",
                trust_score=40.0,
                scan_count=4,
            ),
            McpServerRegistry(
                server_id="tier-source-a-medium",
                name="Source A Medium",
                registry_source="source-a",
                risk_tier="MEDIUM",
                trust_score=70.0,
                scan_count=3,
            ),
            McpServerRegistry(
                server_id="tier-source-b-low",
                name="Source B Low",
                registry_source="source-b",
                risk_tier="LOW",
                trust_score=85.0,
                scan_count=1,
            ),
            McpServerRegistry(
                server_id="tier-source-b-high",
                name="Source B High",
                registry_source="source-b",
                risk_tier="HIGH",
                trust_score=30.0,
                scan_count=5,
            ),
            McpServerRegistry(
                server_id="tier-source-b-medium",
                name="Source B Medium",
                registry_source="source-b",
                risk_tier="MEDIUM",
                trust_score=60.0,
                scan_count=3,
            ),
        ]
    )
    session.commit()
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

    client = TestClient(app)
    response = client.get("/api/tiers/distribution")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_servers"] == 6, body
    assert sum(tier["count"] for tier in body["tiers"]) == 6, body
    assert next(
        tier["count"] for tier in body["tiers"] if tier["risk_tier"] == "HIGH"
    ) == 2, body

    by_source = {entry["registry_source"]: entry for entry in body["by_source"]}
    assert set(by_source) == {"source-a", "source-b"}, body
    assert by_source["source-a"]["total"] == 3, body
    assert by_source["source-b"]["total"] == 3, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)