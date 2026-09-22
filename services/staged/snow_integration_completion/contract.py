# services/staged/snow_integration_completion/contract.py

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


class CompletionResponse(BaseModel):
    total: int
    completed: int
    pending: int


@router.post(
    "/integration/snow/completion",
    response_model=CompletionResponse,
    summary="ServiceNow integration completion status",
)
def get_completion(session: Session = Depends(get_session)):
    """
    Returns the total number of ServiceNow integrations and how many are
    completed vs pending. An integration is considered *completed* when the
    `verdict` column equals ``"completed"``; all other records are treated as
    *pending*.
    """
    base_q = session.query(McpServerRegistry).filter(
        McpServerRegistry.registry_source == "snow"
    )
    total = base_q.count()
    completed = base_q.filter(McpServerRegistry.verdict == "completed").count()
    pending = total - completed
    return CompletionResponse(total=total, completed=completed, pending=pending)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # --------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real DB dependency)
    # --------------------------------------------------------------------- #
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Create the table structure for McpServerRegistry in the in‑memory DB
    McpServerRegistry.__table__.create(bind=test_engine, checkfirst=True)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # --------------------------------------------------------------------- #
    # FastAPI app for the test
    # --------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    # --------------------------------------------------------------------- #
    # Seed test data: 2 completed, 1 pending ServiceNow integrations
    # --------------------------------------------------------------------- #
    with TestSessionLocal() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="s1",
                    name="Server 1",
                    registry_source="snow",
                    verdict="completed",
                ),
                McpServerRegistry(
                    server_id="s2",
                    name="Server 2",
                    registry_source="snow",
                    verdict="completed",
                ),
                McpServerRegistry(
                    server_id="s3",
                    name="Server 3",
                    registry_source="snow",
                    verdict="pending",
                ),
            ]
        )
        db.commit()

    # --------------------------------------------------------------------- #
    # Execute the request and validate the response
    # --------------------------------------------------------------------- #
    client = TestClient(app)
    response = client.post("/api/integration/snow/completion")
    expected = {"total": 3, "completed": 2, "pending": 1}

    if response.status_code == 200 and response.json() == expected:
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL", response.status_code, response.text)
        sys.exit(1)