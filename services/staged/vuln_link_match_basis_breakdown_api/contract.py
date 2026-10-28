# services/staged/vuln_link_match_basis_breakdown_api/contract.py
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List

from app.db import Base, get_session
from app.models import VulnLink

router = APIRouter(prefix="/api")


class BasisBreakdown(BaseModel):
    match_basis: str
    rows: int
    advisories: int
    servers: int
    avg_confidence: float


class BreakdownResponse(BaseModel):
    bases: List[BasisBreakdown]


@router.get(
    "/vuln-links/match-basis-breakdown",
    response_model=BreakdownResponse,
    tags=["vuln_links"],
)
def get_match_basis_breakdown(
    db: Session = Depends(get_session),
) -> BreakdownResponse:
    stmt = (
        db.query(
            VulnLink.match_basis.label("match_basis"),
            func.count().label("rows"),
            func.count(func.distinct(VulnLink.advisory_id)).label("advisories"),
            func.count(func.distinct(VulnLink.server_id)).label("servers"),
            func.avg(VulnLink.match_confidence).label("avg_confidence"),
        )
        .group_by(VulnLink.match_basis)
        .order_by(VulnLink.match_basis)
    )
    results = stmt.all()
    bases = [
        BasisBreakdown(
            match_basis=row.match_basis,
            rows=row.rows,
            advisories=row.advisories,
            servers=row.servers,
            avg_confidence=round(row.avg_confidence, 4) if row.avg_confidence is not None else 0.0,
        )
        for row in results
    ]
    return BreakdownResponse(bases=bases)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.vuln_link_match_basis_breakdown_api.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from datetime import datetime
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # --------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real Postgres dependency)
    # --------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def get_test_session() -> Session:  # pragma: no cover
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Seed data
    with TestingSessionLocal() as db:
        db.add_all(
            [
                VulnLink(
                    id=1,
                    advisory_id=1,
                    server_id=10,
                    match_basis="basis1",
                    match_value="val1",
                    match_confidence=0.8,
                    linked_at=datetime.utcnow(),
                ),
                VulnLink(
                    id=2,
                    advisory_id=2,
                    server_id=10,
                    match_basis="basis1",
                    match_value="val2",
                    match_confidence=0.6,
                    linked_at=datetime.utcnow(),
                ),
                VulnLink(
                    id=3,
                    advisory_id=1,
                    server_id=11,
                    match_basis="basis2",
                    match_value="val3",
                    match_confidence=0.9,
                    linked_at=datetime.utcnow(),
                ),
            ]
        )
        db.commit()

    # Build FastAPI app with overridden dependency
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/vuln-links/match-basis-breakdown")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "bases" in data, "Missing 'bases' key"
    bases = data["bases"]
    assert isinstance(bases, list), "'bases' is not a list"
    assert len(bases) == 2, f"Expected 2 bases, got {len(bases)}"

    # Verify known counts for 'basis1'
    basis1 = next((b for b in bases if b["match_basis"] == "basis1"), None)
    assert basis1 is not None, "'basis1' entry missing"
    assert basis1["rows"] == 2, f"basis1 rows expected 2, got {basis1['rows']}"
    # (additional checks are optional)

    print("PASS")
    sys.exit(0)