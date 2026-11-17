"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Runs the service in an isolated subprocess with an in‑memory SQLite DB,
overrides the real DB dependency, seeds test data, calls the pure logic
function and asserts the expected results.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpScoreDispute

from .logic import aging_report
from .router import router


def _seed_data(session) -> None:
    """Insert four dispute rows: three open of varying ages and one resolved."""
    now = datetime.utcnow()

    # Open dispute – 1 day old
    session.add(
        McpScoreDispute(
            id=1,
            server_id="srv1",
            status="open",
            reason_category="cat_a",
            created_at=now - timedelta(days=1),
            resolved_at=None,
            proposed_overall_risk="LOW",
            explanation="test",
            submitted_by="user1",
        )
    )
    # Open dispute – 10 days old
    session.add(
        McpScoreDispute(
            id=2,
            server_id="srv2",
            status="open",
            reason_category="cat_b",
            created_at=now - timedelta(days=10),
            resolved_at=None,
            proposed_overall_risk="MEDIUM",
            explanation="test",
            submitted_by="user2",
        )
    )
    # Open dispute – 200 days old
    session.add(
        McpScoreDispute(
            id=3,
            server_id="srv3",
            status="open",
            reason_category="cat_a",
            created_at=now - timedelta(days=200),
            resolved_at=None,
            proposed_overall_risk="HIGH",
            explanation="test",
            submitted_by="user3",
        )
    )
    # Resolved dispute – 5 days old (resolved)
    session.add(
        McpScoreDispute(
            id=4,
            server_id="srv4",
            status="resolved",
            reason_category="cat_c",
            created_at=now - timedelta(days=5),
            resolved_at=now - timedelta(days=1),
            proposed_overall_risk="LOW",
            explanation="test",
            submitted_by="user4",
        )
    )
    session.commit()


def run() -> bool:
    """Execute the self‑test."""
    # ------------------------------------------------------------------ #
    # 1️⃣  Build an in‑memory SQLite engine and create tables.
    # ------------------------------------------------------------------ #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # ------------------------------------------------------------------ #
    # 2️⃣  Seed test data.
    # ------------------------------------------------------------------ #
    seed_session = TestingSession()
    _seed_data(seed_session)
    seed_session.close()

    # ------------------------------------------------------------------ #
    # 3️⃣  Dependency override for the FastAPI app.
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
    # 4️⃣  Verify the HTTP endpoint works.
    # ------------------------------------------------------------------ #
    client = TestClient(app)
    resp = client.get("/api/disputes/aging")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Basic shape checks (total counts)
    assert body["total_open"] == 3, body
    assert body["total_resolved"] == 1, body

    # ------------------------------------------------------------------ #
    # 5️⃣  Directly test the pure logic function.
    # ------------------------------------------------------------------ #
    now = datetime.utcnow()
    with TestingSession() as sess:
        rows = sess.execute(select(McpScoreDispute)).scalars().all()
        dict_rows = [
            {
                "status": r.status,
                "reason_category": r.reason_category,
                "created_at": r.created_at,
                "resolved_at": r.resolved_at,
            }
            for r in rows
        ]

    report = aging_report(dict_rows, now)

    # Assertions required by the specification
    assert report["total_open"] == 3, report
    bucket_counts = {b["bucket"]: b["count"] for b in report["age_buckets"]}
    assert bucket_counts.get("90d+", 0) == 1, report
    assert report["oldest_open_days"] >= 200, report
    assert report["total_resolved"] == 1, report

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)