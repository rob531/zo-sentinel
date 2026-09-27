# deps: fastapi, sqlalchemy, pydantic
"""CVE Facet Compile Wiring V2 Service.

Returns a list of CVE facet records grouped by ecosystem, package, severity and source.
"""

from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnerabilityAdvisory

router = APIRouter(prefix="/api", tags=["cve_facet_compile_wiring_v2"])

class CVEFacet(BaseModel):
    ecosystem: str
    package: str
    severity: str
    source: str

    class Config:
        orm_mode = True

def _fetch_cve_facets(session: Session) -> List[CVEFacet]:
    """
    Core data-access routine.
    Queries the `vulnerability_advisory` table and returns a list of
    CVEFacet objects containing ecosystem, package, severity and source.
    """
    rows = (
        session.query(
            VulnerabilityAdvisory.ecosystem,
            VulnerabilityAdvisory.package,
            VulnerabilityAdvisory.severity,
            VulnerabilityAdvisory.source,
        )
        .all()
    )
    return [CVEFacet(**dict(zip(("ecosystem", "package", "severity", "source"), r))) for r in rows]

@router.get("/reports/cve_facet", response_model=List[CVEFacet])
def get_cve_facet(session: Session = Depends(get_session)):
    """
    FastAPI endpoint - GET /api/reports/cve_facet
    Returns all CVE facets present in the database.
    """
    return _fetch_cve_facets(session)

# --------------------------------------------------------------------------- #
# Self-test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from app.db import get_session
    from app.models import Base

    # Create an in-memory SQLite database and initialize the schema
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    # Dependency override providing the test session
    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed the test database with two advisory rows
    with SessionLocal() as db:
        db.execute(
            text(
                """
                INSERT INTO vuln_advisories (ecosystem, package, severity, source)
                VALUES (:ecosystem, :package, :severity, :source)
                """
            ),
            [
                {
                    "ecosystem": "npm",
                    "package": "lodash",
                    "severity": "high",
                    "source": "nvd",
                },
                {
                    "ecosystem": "pypi",
                    "package": "requests",
                    "severity": "medium",
                    "source": "github",
                },
            ],
        )
        db.commit()

    # Build FastAPI app, include router, and apply the dependency override
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # Perform the request and validate the contract
    response = client.get("/api/reports/cve_facet")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()
    assert isinstance(data, list), "Response is not a list"
    assert len(data) == 2, f"Expected 2 records, got {len(data)}"
    assert data[0]["ecosystem"] == "npm", f"Unexpected ecosystem: {data[0]['ecosystem']}"
    assert data[1]["ecosystem"] == "pypi", f"Unexpected ecosystem: {data[1]['ecosystem']}"

    print("PASS")
