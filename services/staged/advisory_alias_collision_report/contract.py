"""Acceptance self-test for the advisory alias collision report service."""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, VulnAdvisory

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add(
        VulnAdvisory(
            id="adv-1",
            feed="osv",
            summary="Advisory one",
            severity="HIGH",
            ecosystem="PyPI",
            package="example-one",
            aliases=["CVE-2024-0001", "GHSA-xxxx", "GHSA-xxxx"],
            identities=[],
            content_hash="hash-one",
            source_url="https://example.test/advisories/adv-1",
        )
    )
    session.add(
        VulnAdvisory(
            id="adv-2",
            feed="github",
            summary="Advisory two",
            severity="MEDIUM",
            ecosystem="npm",
            package="example-two",
            aliases=["CVE-2024-0001"],
            identities=[],
            content_hash="hash-two",
            source_url="https://example.test/advisories/adv-2",
        )
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
    response = client.get("/api/vuln/advisory-alias-collisions")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_aliases"] >= 1, body
    collisions = [
        collision
        for collision in body["collisions"]
        if collision["alias"] == "CVE-2024-0001"
    ]
    assert len(collisions) == 1, body
    assert len(collisions[0]["advisory_ids"]) == 2, body
    assert set(collisions[0]["advisory_ids"]) == {"adv-1", "adv-2"}, body

    duplicates = [
        duplicate
        for duplicate in body["intra_advisory_duplicates"]
        if duplicate["advisory_id"] == "adv-1"
        and duplicate["alias"] == "GHSA-xxxx"
    ]
    assert len(duplicates) == 1, body
    assert duplicates[0]["count"] == 2, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)