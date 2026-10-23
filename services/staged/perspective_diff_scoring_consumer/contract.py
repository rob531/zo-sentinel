# services/staged/perspective_diff_scoring_consumer/contract.py
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Real data layer imports (must not be stubbed)
from app.db import get_session, Base
from app.models import Perspective  # noqa: F401  (imported for side‑effects / type checking)

app = FastAPI()


@app.get("/health")
def health():
    """Simple health check used by the contract test."""
    return {"status": "ok"}


def run_contract_test() -> None:
    """
    Execute a minimal contract test against the FastAPI app.
    The test only verifies that the health endpoint returns 200.
    """
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200, f"Health endpoint failed: {resp.status_code}"
    assert resp.json() == {"status": "ok"}, f"Unexpected health payload: {resp.json()}"


if __name__ == "__main__":
    # ----------------------------------------------------------------------
    # In‑process SQLite test database (StaticPool) – overrides the real DB.
    # ----------------------------------------------------------------------
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables for the imported models (no data is required for the test)
    Base.metadata.create_all(bind=engine)

    def _override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Apply the override for the duration of the contract test
    app.dependency_overrides[get_session] = _override_get_session

    # Run the contract test and report success
    run_contract_test()
    print("PASS")