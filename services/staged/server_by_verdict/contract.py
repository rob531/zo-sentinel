from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any

from app.db import get_session
from app.models import McpServerRegistry, Base

router = APIRouter(prefix="/api/servers", tags=["servers_by_verdict"])


@router.get(
    "/by-verdict",
    response_model=Dict[str, Any],
    summary="Get servers filtered by verdict with pagination",
)
def get_servers_by_verdict(
    verdict: str = Query(..., description="Verdict to filter by"),
    limit: int = Query(100, ge=1, description="Maximum number of servers to return"),
    offset: int = Query(0, ge=0, description="Number of servers to skip"),
    db: Session = Depends(get_session),
):
    query = db.query(McpServerRegistry).filter(McpServerRegistry.verdict == verdict)
    total = query.count()
    rows = (
        query.order_by(McpServerRegistry.server_id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    servers = [
        {
            "server_id": row.server_id,
            "name": row.name,
            "risk_tier": row.risk_tier,
            "trust_score": row.trust_score,
            "last_assessed": row.last_assessed.isoformat()
            if row.last_assessed
            else None,
            "url": row.url,
        }
        for row in rows
    ]
    return {"verdict": verdict, "total": total, "servers": servers}


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.server_by_verdict.contract`) #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    import datetime

    # In‑memory SQLite engine (static pool for thread safety)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=engine)

    # Create tables and seed data
    Base.metadata.create_all(bind=engine)
    test_db: Session = TestSessionLocal()
    seed_data = [
        # TRUSTED_GENERAL
        McpServerRegistry(
            server_id="srv-001",
            name="Trusted General 1",
            risk_tier="LOW",
            trust_score=95,
            last_assessed=datetime.datetime.utcnow(),
            url="https://trusted1.example.com",
            verdict="TRUSTED_GENERAL",
        ),
        McpServerRegistry(
            server_id="srv-002",
            name="Trusted General 2",
            risk_tier="LOW",
            trust_score=92,
            last_assessed=datetime.datetime.utcnow(),
            url="https://trusted2.example.com",
            verdict="TRUSTED_GENERAL",
        ),
        # HIGH_RISK_ISOLATED
        McpServerRegistry(
            server_id="srv-003",
            name="High Risk 1",
            risk_tier="HIGH",
            trust_score=20,
            last_assessed=datetime.datetime.utcnow(),
            url="https://highrisk1.example.com",
            verdict="HIGH_RISK_ISOLATED",
        ),
        McpServerRegistry(
            server_id="srv-004",
            name="High Risk 2",
            risk_tier="HIGH",
            trust_score=15,
            last_assessed=datetime.datetime.utcnow(),
            url="https://highrisk2.example.com",
            verdict="HIGH_RISK_ISOLATED",
        ),
        # KNOWN_THREAT
        McpServerRegistry(
            server_id="srv-005",
            name="Known Threat 1",
            risk_tier="CRITICAL",
            trust_score=5,
            last_assessed=datetime.datetime.utcnow(),
            url="https://threat1.example.com",
            verdict="KNOWN_THREAT",
        ),
        McpServerRegistry(
            server_id="srv-006",
            name="Known Threat 2",
            risk_tier="CRITICAL",
            trust_score=3,
            last_assessed=datetime.datetime.utcnow(),
            url="https://threat2.example.com",
            verdict="KNOWN_THREAT",
        ),
    ]
    test_db.add_all(seed_data)
    test_db.commit()

    # Build FastAPI app with dependency override
    app = FastAPI()
    app.include_router(router)

    def get_test_session() -> Session:  # pragma: no cover
        return test_db

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # Expected counts per verdict
    expected_counts = {
        "TRUSTED_GENERAL": 2,
        "HIGH_RISK_ISOLATED": 2,
        "KNOWN_THREAT": 2,
    }

    for verdict, expected_total in expected_counts.items():
        resp = client.get(
            "/api/servers/by-verdict",
            params={"verdict": verdict, "limit": 100, "offset": 0},
        )
        assert resp.status_code == 200, f"Unexpected status for {verdict}"
        data = resp.json()
        assert data["verdict"] == verdict
        assert data["total"] == expected_total
        assert len(data["servers"]) == expected_total

        # Pagination sanity check (limit=1, offset=1 should return the second record)
        if expected_total > 1:
            paged = client.get(
                "/api/servers/by-verdict",
                params={"verdict": verdict, "limit": 1, "offset": 1},
            )
            assert paged.status_code == 200
            paged_data = paged.json()
            assert paged_data["total"] == expected_total
            assert len(paged_data["servers"]) == 1

    print("PASS")