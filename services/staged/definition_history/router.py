from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import get_session, Base
from app.models import McpDefinitionHistory  # noqa: F401 (used in tests)

router = APIRouter(prefix="/api", tags=["definition_history"])


@router.get("/history")
def get_history(server: int, db: Session = Depends(get_session)):
    """
    Retrieve the definition change history for a given server.

    Returns a JSON object:
    {
        "server": <int>,
        "timeline": [
            {"date": "<iso>", "change": "<description>"},
            ...
        ]
    }
    """
    from .logic import get_definition_history  # Imported lazily to avoid circular imports
    return get_definition_history(db, server)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # ------------------------------------------------------------------- #
    # Build a temporary in‑memory SQLite DB and populate it with test data
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(bind=engine)

    # Insert two definition changes for server id 1
    with SessionLocal() as db:
        db.add_all(
            [
                McpDefinitionHistory(
                    server_id=1,
                    change_date="2023-01-01T12:00:00Z",
                    change_description="Initial definition",
                ),
                McpDefinitionHistory(
                    server_id=1,
                    change_date="2023-02-15T08:30:00Z",
                    change_description="Updated configuration",
                ),
            ]
        )
        db.commit()

    # ------------------------------------------------------------------- #
    # Override the FastAPI dependency to use the temporary session
    # ------------------------------------------------------------------- #
    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Run the test client against the endpoint
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    resp = client.get("/api/history", params={"server": 1})
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    assert payload["server"] == 1, "Server ID mismatch"
    assert isinstance(payload["timeline"], list), "Timeline not a list"
    assert len(payload["timeline"]) == 2, "Timeline length mismatch"
    print("PASS")