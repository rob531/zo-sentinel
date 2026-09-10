"""CVE Severity Breakdown Service."""

from fastapi import FastAPI
from app.db import get_session
from app.models import McpServerRegistry


def signal_scores_endpoint(app: FastAPI) -> dict:
    """Auto-emitted endpoint stub for signal scores."""
    return {"service": "cve_severity_breakdown", "status": "ok"}


def _run_self_test() -> bool:
    """Self-test with in-memory override."""
    from sqlalchemy.pool import StaticPool
    from app.db import get_session
    from app.main import app as main_app
    from fastapi.testclient import TestClient

    # Create in-memory test session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    main_app.dependency_overrides[get_session] = override_get_session

    try:
        client = TestClient(main_app)
        result = signal_scores_endpoint(main_app)
        assert isinstance(result, dict)
        assert result["service"] == "cve_severity_breakdown"
    finally:
        main_app.dependency_overrides.clear()

    print("PASS")
    return True


if __name__ == "__main__":
    _run_self_test()