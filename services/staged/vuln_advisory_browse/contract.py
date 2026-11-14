"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite DB.
Seeds test data, overrides the DB dependency, hits the endpoint and asserts
the expected response shape and content.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, VulnAdvisory, VulnLink
    from .router import router

    # ------------------------------------------------------------------ #
    # In‑memory SQLite engine and session factory
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # Seed test data
    # ------------------------------------------------------------------ #
    sess = TestingSession()
    # Advisories
    adv1 = VulnAdvisory(
        id="adv1",
        feed="feed1",
        summary="summary 1",
        severity="HIGH",
        source_url="http://example.com/1",
        ecosystem="npm",
        package="pkg1",
        published_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
    )
    adv2 = VulnAdvisory(
        id="adv2",
        feed="feed2",
        summary="summary 2",
        severity="LOW",
        source_url="http://example.com/2",
        ecosystem="pypi",
        package="pkg2",
        published_at=datetime(2023, 1, 2, tzinfo=timezone.utc),
    )
    adv3 = VulnAdvisory(
        id="adv3",
        feed="feed3",
        summary="summary 3",
        severity="MEDIUM",
        source_url="http://example.com/3",
        ecosystem="rubygems",
        package="pkg3",
        published_at=datetime(2023, 1, 3, tzinfo=timezone.utc),
    )
    sess.add_all([adv1, adv2, adv3])

    # Links (both point to adv1)
    link1 = VulnLink(
        advisory_id="adv1",
        server_id="srv1",
        match_basis="basis1",
        match_value="value1",
        match_confidence=0.9,
    )
    link2 = VulnLink(
        advisory_id="adv1",
        server_id="srv2",
        match_basis="basis2",
        match_value="value2",
        match_confidence=0.8,
    )
    sess.add_all([link1, link2])
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # Dependency override for the FastAPI app
    # ------------------------------------------------------------------ #
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------ #
    # Execute acceptance checks
    # ------------------------------------------------------------------ #
    client = TestClient(app)

    # 1. Unfiltered request – expect all three advisories
    resp = client.get("/api/vulns/advisories")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 3, body
    advs = {a["id"]: a for a in body["advisories"]}

    # Advisory with two links should report link_count == 2
    assert advs["adv1"]["link_count"] == 2, advs["adv1"]

    # 2. Severity filter – should narrow to exactly one advisory (LOW)
    resp = client.get("/api/vulns/advisories?severity=LOW")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1, body
    assert body["advisories"][0]["id"] == "adv2", body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)