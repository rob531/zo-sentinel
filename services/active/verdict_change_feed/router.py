# deps: fastapi, sqlalchemy, pydantic
"""Verdict Change Feed Service

Provides a simple endpoint to retrieve recent server verdict changes.
Public endpoint – no authentication required.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Import the shared DB session dependency and ORM models
from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/verdict_change_feed", tags=["verdict_change_feed"])


class VerdictChange(BaseModel):
    server_id: str = Field(..., description="Unique identifier of the server")
    name: str | None = Field(None, description="Human‑readable name of the server")
    verdict: str | None = Field(None, description="Current verdict (e.g., safe, risky)")
    last_scanned: str | None = Field(
        None, description="ISO‑8601 timestamp of the most recent scan"
    )

    class Config:
        orm_mode = True


@router.get("/", response_model=list[VerdictChange])
def get_recent_verdict_changes(
    limit: int = 10,
    db: Session = Depends(get_session),
):
    """Return the most recent server verdict changes.

    The endpoint queries the ``McpServerRegistry`` table ordered by ``last_scanned``
    descending and returns up to ``limit`` rows.
    """
    if limit <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="limit must be positive",
        )
    # Query the registry for recent changes
    rows = (
        db.query(McpServerRegistry)
        .order_by(McpServerRegistry.last_scanned.desc())
        .limit(limit)
        .all()
    )
    return rows


# Self‑test ---------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Create a minimal FastAPI app and include the router
    app = FastAPI()
    app.include_router(router)

    # Stub session that returns an empty list for any query
    class _StubSession:
        def query(self, *args, **kwargs):
            class _Query:
                def order_by(self, *a, **kw):
                    return self

                def limit(self, *a, **kw):
                    return self

                def all(self):
                    return []

            return _Query()

    def get_stub_session():
        return _StubSession()

    # Override the DB dependency with the stub
    app.dependency_overrides[get_session] = get_stub_session

    client = TestClient(app)
    try:
        resp = client.get("/verdict_change_feed/")
        assert resp.status_code == 200, f"unexpected status {resp.status_code}"
        assert isinstance(resp.json(), list), "response is not a list"
        print("PASS")
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)
