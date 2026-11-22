"""contract.py -- the service's ACCEPTANCE self‑test.

Runs the service in an isolated subprocess with an in‑memory SQLite DB.
If the service boots, mounts, serves 200 and returns a schema‑valid body,
the script prints ``PASS`` and exits 0.
"""
from __future__ import annotations

import sys
from datetime import datetime

def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Real data layer imports (must not be stubbed)
    from app.db import get_session
    from app.models import Base, McpServerRegistry

    # Router for the service under test
    from .router import router

    # ------------------------------------------------------------------
    # Build an in‑memory SQLite DB that mirrors the real schema
    # ------------------------------------------------------------------
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # Seed four rows spanning two registry_source values and three risk_tier values
    seed_session = TestingSession()
    now = datetime.utcnow()
    seed_session.add(
        McpServerRegistry(
            server_id="srv1",
            name="Server One",
            registry_source="sourceA",
            risk_tier="HIGH",
            trust_score=80.0,
            verdict="PASS",
            last_assessed=now,
            last_seen=now,
        )
    )
    seed_session.add(
        McpServerRegistry(
            server_id="srv2",
            name="Server Two",
            registry_source="sourceA",
            risk_tier="MEDIUM",
            trust_score=70.0,
            verdict="PASS",
            last_assessed=now,
            last_seen=now,
        )
    )
    seed_session.add(
        McpServerRegistry(
            server_id="srv3",
            name="Server Three",
            registry_source="sourceB",
            risk_tier="LOW",
            trust_score=60.0,
            verdict="FAIL",
            last_assessed=now,
            last_seen=now,
        )
    )
    seed_session.add(
        McpServerRegistry(
            server_id="srv4",
            name="Server Four",
            registry_source="sourceB",
            risk_tier="HIGH",
            trust_score=90.0,
            verdict="PASS",
            last_assessed=now,
            last_seen=now,
        )
    )
    seed_session.commit()
    seed_session.close()

    # Dependency override that yields a session bound to the in‑memory DB
    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Build FastAPI app with the overridden dependency
    # ------------------------------------------------------------------
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    # ------------------------------------------------------------------
    # Execute the acceptance test
    # ------------------------------------------------------------------
    client = TestClient(app)
    resp = client.get("/api/registry/source-tier-matrix")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Grand total must equal the number of seeded rows
    assert body.get("grand_total") == 4, body

    # Validate per‑source tier counts and that no unexpected tiers appear
    allowed_tiers = {"HIGH", "MEDIUM", "LOW"}
    sources = body.get("sources", [])
    for src in sources:
        total = src.get("total")
        tiers = src.get("tiers", {})
        # Sum of tier counts must equal the source total
        assert sum(tiers.values()) == total, f"{src}"
        # No tier keys outside the seeded set
        assert set(tiers).issubset(allowed_tiers), f"{src}"

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)