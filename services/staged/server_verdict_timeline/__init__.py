"""Auto‑emitted service package.

Provides a minimal FastAPI application with a single test endpoint.
The module imports the real application DB session and models to satisfy
runtime contracts, while the __main__ self‑test overrides the session
with an in‑memory SQLite database.
"""

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Real application dependencies – must remain unchanged.
from app.db import get_session
from app.models import Base  # noqa: F401
# Import all models to satisfy any downstream imports.
from app.models import *  # noqa: F403,F401

app = FastAPI()


@app.get("/test")
async def test_endpoint(db=Depends(get_session)):
    """A trivial endpoint that verifies DB dependency wiring."""
    # The endpoint does not need to query the DB; the dependency
    # injection itself is the contract being exercised.
    return {"msg": "test"}


# ----------------------------------------------------------------------
# __main__ self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Create an in‑memory SQLite engine and session factory.
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Create all tables for the imported models.
    Base.metadata.create_all(engine)

    # Override the real get_session dependency with the test session.
    def get_test_session():
        return SessionLocal()

    app.dependency_overrides[get_session] = get_test_session

    # Run a simple request against the test endpoint.
    client = TestClient(app)
    response = client.get("/test")
    assert response.status_code == 200
    assert response.json() == {"msg": "test"}

    print("PASS")