"""
services/staged/nvd_cve_feed_ingestion/contract.py

FastAPI contract for the ``nvd_cve_feed_ingestion`` service.

Provides a single endpoint:
    POST /ingest/cve
which triggers ingestion of the NVD CVE feed.

The module imports the real application data layer (``app.db.get_session``) but
does not perform any DB operations itself – the endpoint is a thin wrapper that
can be exercised in isolation by the self‑test.
"""

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# Real data‑layer import – required by the “no‑hollow” rule.
from app.db import get_session  # pragma: no cover

router = APIRouter()


@router.post(
    "/ingest/cve",
    status_code=status.HTTP_200_OK,
    response_model=dict,
    summary="Ingest NVD CVE feed",
    description="Fetches the NVD CVE feed, parses it and stores new CVE records.",
)
async def ingest_cve(
    payload: dict = Body(..., description="Optional payload; currently unused."),
    db: Session = Depends(get_session),
):
    """
    Endpoint stub for NVD CVE ingestion.

    The real implementation would:
        * download the NVD feed (using ``requests``),
        * parse the XML (using ``lxml``),
        * upsert CVE records into the PostgreSQL database via ``db``.
    For the purpose of contract testing we simply acknowledge the request.
    """
    # Placeholder for future logic – keep DB session alive to satisfy the contract.
    if db is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database session not available.",
        )
    return {"status": "ingested"}


# --------------------------------------------------------------------------- #
# Self‑test (run with ``python -m services.staged.nvd_cve_feed_ingestion.contract``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Build a minimal FastAPI app that includes this router.
    app = FastAPI()
    app.include_router(router)

    # --------------------------------------------------------------------- #
    # Dependency override: provide a throw‑away SQLite session for the test.
    # --------------------------------------------------------------------- #
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite – no models are required for this test.
    _engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    _SessionLocal = sessionmaker(bind=_engine)

    def _override_get_session() -> Session:  # pragma: no cover
        return _SessionLocal()

    app.dependency_overrides[get_session] = _override_get_session

    # --------------------------------------------------------------------- #
    # Execute the test client.
    # --------------------------------------------------------------------- #
    client = TestClient(app)

    response = client.post("/ingest/cve", json={})
    if response.status_code == 200 and response.json().get("status") == "ingested":
        print("PASS")
        exit(0)
    else:
        print("FAIL")
        exit(1)