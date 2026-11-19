from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry, McpUnauthAccess

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
    session.add(
        McpServerRegistry(
            server_id="srv-matched",
            name="Matched Server",
            risk_tier="HIGH_RISK_ISOLATED",
            trust_score=0.25,
        )
    )
    session.add_all(
        [
            McpUnauthAccess(
                server_id="srv-matched",
                url="https://matched.example/mcp",
                unauth_access="accepted",
                source="probe",
            ),
            McpUnauthAccess(
                server_id="srv-unmatched",
                url="https://unmatched.example/mcp",
                unauth_access="accepted",
                source="probe",
            ),
            McpUnauthAccess(
                server_id="srv-other",
                url="https://other.example/mcp",
                unauth_access="rejected",
                source="scan",
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

    response = client.get("/api/security/unauth-access")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 3, body
    assert len(body["items"]) == 3, body

    matched = next(
        item for item in body["items"] if item["server_id"] == "srv-matched"
    )
    assert matched["name"] == "Matched Server", matched
    assert matched["risk_tier"] == "HIGH_RISK_ISOLATED", matched
    assert matched["trust_score"] == 0.25, matched

    unmatched = next(
        item for item in body["items"] if item["server_id"] == "srv-unmatched"
    )
    assert unmatched["name"] is None, unmatched
    assert unmatched["risk_tier"] is None, unmatched
    assert unmatched["trust_score"] is None, unmatched

    filtered_response = client.get(
        "/api/security/unauth-access",
        params={"server_id": "srv-matched"},
    )
    assert filtered_response.status_code == 200, filtered_response.text
    filtered_body = filtered_response.json()
    assert filtered_body["count"] == 1, filtered_body
    assert len(filtered_body["items"]) == 1, filtered_body
    assert filtered_body["items"][0]["server_id"] == "srv-matched", filtered_body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)