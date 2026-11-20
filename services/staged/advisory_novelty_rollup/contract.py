"""contract.py -- acceptance self‑test for advisory_novelty_rollup.

Runs the service in an isolated subprocess with an in‑memory SQLite DB,
seeds a few VulnAdvisory rows and asserts the API returns the expected
shape and counts.

Usage:
    python -m services.staged.advisory_novelty_rollup.contract
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Real data layer imports – required for a non‑hollow build
from app.db import get_session
from app.models import Base, VulnAdvisory

# Router under test
from .router import router


def run() -> bool:
    """Boot the service with an in‑memory DB, seed data, and validate the endpoint."""
    # ------------------------------------------------------------------ #
    # 1️⃣  In‑memory SQLite engine & session factory
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # 2️⃣  Seed four VulnAdvisory rows covering the required cases
    # ------------------------------------------------------------------ #
    now = datetime.utcnow()
    cutoff = now - timedelta(days=30)

    rows = [
        # Recent advisory – counts toward published, fetched, severity, ecosystem, feed
        VulnAdvisory(
            id="adv1",
            feed="feed1",
            summary="recent high npm",
            severity="HIGH",
            ecosystem="npm",
            package="pkg1",
            affected_ranges="[]",
            aliases="[]",
            source_url="https://example.com/adv1",
            published_at=now - timedelta(days=5),
            fetched_at=now - timedelta(days=5),
            identities="{}",
            content_hash="hash1",
        ),
        # Recent advisory – different severity/ecosystem/feed
        VulnAdvisory(
            id="adv2",
            feed="feed2",
            summary="recent medium pypi",
            severity="MEDIUM",
            ecosystem="pypi",
            package="pkg2",
            affected_ranges="[]",
            aliases="[]",
            source_url="https://example.com/adv2",
            published_at=now - timedelta(days=10),
            fetched_at=now - timedelta(days=10),
            identities="{}",
            content_hash="hash2",
        ),
        # Backfilled advisory – old publish, recent fetch
        VulnAdvisory(
            id="adv3",
            feed="feed1",
            summary="backfill low npm",
            severity="LOW",
            ecosystem="npm",
            package="pkg3",
            affected_ranges="[]",
            aliases="[]",
            source_url="https://example.com/adv3",
            published_at=now - timedelta(days=40),
            fetched_at=now - timedelta(days=5),
            identities="{}",
            content_hash="hash3",
        ),
        # Stale advisory – outside both windows, should not affect any count
        VulnAdvisory(
            id="adv4",
            feed="feed3",
            summary="old adv",
            severity="LOW",
            ecosystem="rubygems",
            package="pkg4",
            affected_ranges="[]",
            aliases="[]",
            source_url="https://example.com/adv4",
            published_at=now - timedelta(days=60),
            fetched_at=now - timedelta(days=60),
            identities="{}",
            content_hash="hash4",
        ),
    ]

    sess = TestingSession()
    sess.add_all(rows)
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------ #
    # 3️⃣  Dependency override so the service uses the in‑memory session
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
    # 4️⃣  Exercise the endpoint and assert the contract
    # ------------------------------------------------------------------ #
    client = TestClient(app)
    resp = client.get("/api/advisories/novelty?days=30")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # shape checks
    assert isinstance(body.get("days"), int), body
    assert isinstance(body.get("total_published"), int), body
    assert isinstance(body.get("total_fetched"), int), body
    assert isinstance(body.get("backfilled"), int), body
    assert isinstance(body.get("by_severity"), list), body
    assert isinstance(body.get("by_ecosystem"), list), body
    assert isinstance(body.get("by_feed"), list), body

    # expected numeric values
    assert body["total_published"] == 2, body
    assert body["total_fetched"] == 3, body
    assert body["backfilled"] == 1, body

    # each severity entry must have count >= 1
    for entry in body["by_severity"]:
        assert entry["count"] >= 1, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)