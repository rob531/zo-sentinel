from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_org_risk_summary

router = APIRouter(prefix="/api")


@router.get("/org/{org_id}/risk_summary")
def org_risk_summary(org_id: str, session: Session = Depends(get_session)):
    return get_org_risk_summary(org_id, session)


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app.models import McpServerRegistry, McpLlmAxisScore

    # In‑memory SQLite setup
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()

    # Seed data
    servers = [
        McpServerRegistry(
            server_id="srv1",
            org_id="test_org",
            risk_tier="HIGH_RISK_ISOLATED",
            verdict="malicious",
            confidence=0.9,
            last_assessed="2023-01-01T00:00:00Z",
        ),
        McpServerRegistry(
            server_id="srv2",
            org_id="test_org",
            risk_tier="LOW_RISK",
            verdict="benign",
            confidence=0.8,
            last_assessed="2023-01-02T00:00:00Z",
        ),
        McpServerRegistry(
            server_id="srv3",
            org_id="test_org",
            risk_tier="LOW_RISK",
            verdict="benign",
            confidence=0.85,
            last_assessed="2023-01-03T00:00:00Z",
        ),
    ]
    db.add_all(servers)
    db.commit()

    # FastAPI app wiring
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: db

    client = TestClient(app)
    resp = client.get("/api/org/test_org/risk_summary")
    if resp.status_code != 200:
        print(f"FAIL: Unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    try:
        assert data["total_servers"] == 3
        assert data["tier_counts"]["HIGH_RISK_ISOLATED"] == 1
        assert abs(data["average_confidence"] - 0.85) < 0.01
    except AssertionError:
        print("FAIL: Assertion error", file=sys.stderr)
        sys.exit(1)

    print("PASS")