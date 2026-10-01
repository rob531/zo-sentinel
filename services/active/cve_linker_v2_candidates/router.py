# deps: fastapi, sqlalchemy, requests
"""Router for cve_linker_v2_candidates service."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, not_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


router = APIRouter(prefix="/api", tags=["cve_linker_v2_candidates"])


class CandidateLink(BaseModel):
    server_id: int
    server_name: str
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    suggested_advisory_id: Optional[int] = None
    advisory_summary: Optional[str] = None

    class Config:
        from_attributes = True


@router.get("/cve_linker_v2_candidates", response_model=List[CandidateLink])
def get_candidates(
    ecosystem: Optional[str] = Query(None, description="Filter by ecosystem"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_session),
):
    """Return candidate servers that could be linked to vulnerability advisories.

    A candidate is a server that has package or ecosystem metadata but is not
    yet linked to any VulnAdvisory via VulnLink.
    """
    # Subquery: server_ids that already have links
    linked_server_ids = (
        db.query(VulnLink.server_id)
        .distinct()
        .subquery()
    )

    # Query servers not yet linked
    query = (
        db.query(McpServerRegistry)
        .filter(
            and_(
                McpServerRegistry.risk_tier.isnot(None),
                not_(McpServerRegistry.server_id.in_(
                    db.query(linked_server_ids)
                )),
            )
        )
    )

    if ecosystem:
        query = query.filter(McpServerRegistry.meta.op("->>")("ecosystem") == ecosystem)

    servers = query.limit(limit).all()

    candidates = []
    for srv in servers:
        meta = srv.meta or {}
        srv_ecosystem = meta.get("ecosystem")
        srv_package = meta.get("package")

        # Find a matching advisory if ecosystem+package known
        advisory = None
        if srv_ecosystem and srv_package:
            advisory = (
                db.query(VulnAdvisory)
                .filter(
                    and_(
                        VulnAdvisory.ecosystem == srv_ecosystem,
                        VulnAdvisory.package == srv_package,
                    )
                )
                .first()
            )

        candidates.append(CandidateLink(
            server_id=srv.server_id,
            server_name=srv.name,
            ecosystem=srv_ecosystem,
            package=srv_package,
            suggested_advisory_id=advisory.id if advisory else None,
            advisory_summary=advisory.summary if advisory else None,
        ))

    return candidates


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    with SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO mcp_server_registry "
                "(server_id, name, risk_tier, meta) VALUES (:sid, :name, :tier, :meta)"
            ),
            [
                {"sid": 1, "name": "npm-scanner", "tier": "medium", "meta": '{"ecosystem":"npm","package":"lodash"}'},
                {"sid": 2, "name": "pypi-check", "tier": "low", "meta": '{"ecosystem":"pypi","package":"requests"}'},
                {"sid": 3, "name": "unlinked-server", "tier": "high", "meta": '{"ecosystem":"npm","package":"express"}'},
            ],
        )
        db.execute(
            text(
                "INSERT INTO vuln_advisories "
                "(id, ecosystem, package, summary) VALUES (:id, :eco, :pkg, :sum)"
            ),
            {"id": 10, "eco": "npm", "pkg": "lodash", "sum": "Prototype pollution in lodash"},
        )
        db.commit()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # Test 1: basic candidates
    resp = client.get("/api/cve_linker_v2_candidates")
    assert resp.status_code == 200, f"status {resp.status_code}"
    data = resp.json()
    assert isinstance(data, list), "expected list"
    assert len(data) == 3, f"expected 3 candidates, got {len(data)}"
    assert data[0]["server_name"] == "npm-scanner"

    # Test 2: ecosystem filter
    resp2 = client.get("/api/cve_linker_v2_candidates?ecosystem=npm")
    assert resp2.status_code == 200
    d2 = resp2.json()
    assert all(r["ecosystem"] == "npm" for r in d2), "filter mismatch"

    # Test 3: suggested advisory populated
    npm_candidate = next(r for r in d2 if r["ecosystem"] == "npm" and r.get("package") == "lodash")
    assert npm_candidate["suggested_advisory_id"] == 10, "advisory link missing"

    print("PASS")
