# services/staged/github_pr_checker_wiring/contract.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


@router.get("/integration/github/pr-checker")
def get_github_pr_checker_status(session: Session = Depends(get_session)):
    total = session.query(McpServerRegistry).count()
    passed = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.verdict == "pass")
        .count()
    )
    failed = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.verdict == "fail")
        .count()
    )
    return {"checker_status": {"total": total, "passed": passed, "failed": failed}}


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import Base  # declarative base used by the real models

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real DB dependency)
    # ------------------------------------------------------------------- #
    SQLITE_URL = "sqlite://"
    engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def get_test_session() -> Session:  # pragma: no cover
        return TestSessionLocal()

    # ------------------------------------------------------------------- #
    # Seed test data: 3 passed, 1 failed
    # ------------------------------------------------------------------- #
    def seed():
        with TestSessionLocal() as s:
            rows = [
                McpServerRegistry(
                    server_id=f"srv-{i}",
                    name=f"server-{i}",
                    verdict="pass",
                    confidence=None,
                    description=None,
                    first_seen=None,
                    last_assessed=None,
                    last_scanned=None,
                    last_seen=None,
                    meta=None,
                    registry_source=None,
                    risk_tier=None,
                    scan_count=None,
                    trust_score=None,
                    url=None,
                    verdict_reasoning=None,
                )
                for i in range(3)
            ]
            rows.append(
                McpServerRegistry(
                    server_id="srv-fail",
                    name="server-fail",
                    verdict="fail",
                    confidence=None,
                    description=None,
                    first_seen=None,
                    last_assessed=None,
                    last_scanned=None,
                    last_seen=None,
                    meta=None,
                    registry_source=None,
                    risk_tier=None,
                    scan_count=None,
                    trust_score=None,
                    url=None,
                    verdict_reasoning=None,
                )
            )
            s.add_all(rows)
            s.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with overridden dependency
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed data
    seed()

    # Run test client
    client = TestClient(app)
    resp = client.get("/api/integration/github/pr-checker")
    expected = {"checker_status": {"total": 4, "passed": 3, "failed": 1}}
    if resp.status_code == 200 and resp.json() == expected:
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL", resp.status_code, resp.text, file=sys.stderr)
        sys.exit(1)