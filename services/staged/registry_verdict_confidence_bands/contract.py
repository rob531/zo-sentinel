"""Acceptance self-test for the registry verdict confidence bands service."""
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
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="confidence-band-01",
                name="Confidence Band Server 1",
                verdict="TRUSTED_GENERAL",
                confidence=0.1,
                trust_score=0.8,
                risk_tier="LOW",
            ),
            McpServerRegistry(
                server_id="confidence-band-02",
                name="Confidence Band Server 2",
                verdict="CAUTION_LIMITED",
                confidence=0.4,
                trust_score=0.6,
                risk_tier="MEDIUM",
            ),
            McpServerRegistry(
                server_id="confidence-band-03",
                name="Confidence Band Server 3",
                verdict="TRUSTED_GENERAL",
                confidence=0.6,
                trust_score=0.9,
                risk_tier="LOW",
            ),
            McpServerRegistry(
                server_id="confidence-band-04",
                name="Confidence Band Server 4",
                verdict="CAUTION_LIMITED",
                confidence=0.9,
                trust_score=0.4,
                risk_tier="HIGH",
            ),
        ]
    )
    session.commit()
    session.close()

    def override_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session

    client = TestClient(app)
    response = client.get("/api/registry/verdict-confidence-bands")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 4, body

    bands = body["bands"]
    assert isinstance(bands, list), body
    counts = []
    for band in bands:
        assert isinstance(band, dict), body
        counts.append(band["count"])
        assert isinstance(band["count"], int), body
    assert sum(counts) == 4, body

    band_counts = {}
    for band in bands:
        label = (
            band.get("band")
            or band.get("label")
            or band.get("name")
            or band.get("range")
        )
        if label is not None:
            band_counts[label] = band["count"]
    assert band_counts.get("0.50-0.75") == 1, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)