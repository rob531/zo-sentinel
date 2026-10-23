# services/staged/axis_confidence_service/contract.py
"""
Contract module for the ``axis_confidence_service`` staged service.

Provides a FastAPI endpoint that returns all LLM axis confidence scores
from the application database.  The module can be executed directly to
run a self‑test that validates the contract against an in‑memory SQLite
database using the real SQLAlchemy models.
"""

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

# Real data layer imports – never a stub or mock.
from app.db import get_session, Base
from app.models import McpLlmAxisScore

app = FastAPI()


def _serialize(row: McpLlmAxisScore) -> dict:
    """Convert a SQLAlchemy model instance to a plain dict."""
    data = dict(row.__dict__)
    data.pop("_sa_instance_state", None)
    return data


@app.get("/axis_confidence")
def get_axis_confidence(session: Session = Depends(get_session)):
    """
    Return a list of all ``McpLlmAxisScore`` records serialized as dictionaries.
    """
    records = session.query(McpLlmAxisScore).all()
    return [_serialize(r) for r in records]


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Create an in‑memory SQLite engine with a static pool so that the same
    # connection is reused across sessions (required for FastAPI's dependency
    # overrides to work correctly in the test client).
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Create all tables defined in the real models.
    Base.metadata.create_all(bind=engine)

    # Session factory bound to the in‑memory engine.
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the ``get_session`` dependency to use the test SQLite session.
    def _override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _override_get_session

    # Run the FastAPI test client against the overridden app.
    client = TestClient(app)
    response = client.get("/axis_confidence")

    if response.status_code == 200:
        print("PASS")
        exit(0)
    else:
        print("FAIL")
        exit(1)