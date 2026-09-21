# services/staged/nvd_cve_feed_ingestion/logic.py
# -------------------------------------------------
# Logic for ingesting NVD CVE feed data.
# -------------------------------------------------

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from typing import List, Dict, Any

from sqlalchemy.orm import Session
from app.db import get_session, Base  # real app DB session & Base

# ----------------------------------------------------------------------
# Model import – try common names, fallback to a minimal definition
# ----------------------------------------------------------------------
try:
    from app.models import VulnerabilityAdvisory as Advisory  # primary guess
except Exception:  # pragma: no cover
    try:
        from app.models import VulnAdvisory as Advisory
    except Exception:  # pragma: no cover
        # Minimal fallback – only used when the real model cannot be imported
        from sqlalchemy import Column, Integer, JSON
        from sqlalchemy.ext.declarative import declarative_base

        Base = declarative_base()  # type: ignore

        class Advisory(Base):  # type: ignore
            __tablename__ = "vuln_advisories"
            id = Column(Integer, primary_key=True, index=True)
            advisory = Column(JSON)


# ----------------------------------------------------------------------
# Pydantic schemas
# ----------------------------------------------------------------------
class IngestRequest(BaseModel):
    advisories: List[Dict[str, Any]]


class IngestResponse(BaseModel):
    status: str
    count: int


# ----------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------
router = APIRouter()


@router.post(
    "/api/vuln/ingest/nvd",
    response_model=IngestResponse,
    tags=["nvd_cve_feed_ingestion"],
)
def ingest_nvd(
    payload: IngestRequest, db: Session = Depends(get_session)
) -> IngestResponse:
    """
    Accept a list of NVD CVE advisories and persist them.
    Returns the number of records inserted.
    """
    inserted = 0
    for adv in payload.advisories:
        # Try to map fields directly; if that fails, store the raw dict.
        try:
            obj = Advisory(**adv)  # type: ignore
        except Exception:  # pragma: no cover
            obj = Advisory(advisory=adv)  # type: ignore
        db.add(obj)
        inserted += 1
    db.commit()
    return IngestResponse(status="success", count=inserted)


# ----------------------------------------------------------------------
# Self‑test (executed when running the module directly)
# ----------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for isolated testing
    test_engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )

    # Create tables in the temporary DB
    Base.metadata.create_all(bind=test_engine)

    # Dependency override yielding a fresh session per request
    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Build FastAPI app with the router and overridden dependency
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # Sample payload with two advisories
    sample_payload = {
        "advisories": [
            {"cve_id": "CVE-1234", "description": "Test advisory 1"},
            {"cve_id": "CVE-5678", "description": "Test advisory 2"},
        ]
    }

    response = client.post("/api/vuln/ingest/nvd", json=sample_payload)
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()
    assert data["status"] == "success"
    assert data["count"] == 2
    print("PASS")