from __future__ import annotations
from collections import defaultdict
from typing import Dict, List

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


class MatrixRow(BaseModel):
    severity: str
    tiers: Dict[str, int]


class MatrixResponse(BaseModel):
    rows: List[MatrixRow]


def compute_matrix(db: Session) -> List[MatrixRow]:
    stmt = (
        select(
            VulnAdvisory.severity,
            McpServerRegistry.risk_tier,
            func.count(VulnAdvisory.id),
        )
        .select_from(VulnAdvisory)
        .join(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
        .join(McpServerRegistry, McpServerRegistry.server_id == VulnLink.server_id)
        .group_by(VulnAdvisory.severity, McpServerRegistry.risk_tier)
    )
    raw = db.execute(stmt).all()

    by_severity: Dict[str, Dict[str, int]] = defaultdict(dict)
    for severity, risk_tier, count in raw:
        by_severity[severity][risk_tier] = int(count)

    return [
        MatrixRow(severity=sev, tiers=dict(tiers))
        for sev, tiers in by_severity.items()
    ]


router = APIRouter(prefix="/api")


@router.get("/cve/risk-tier-severity-matrix", response_model=MatrixResponse)
def get_matrix(db: Session = Depends(get_session)) -> MatrixResponse:
    return MatrixResponse(rows=compute_matrix(db))


app = FastAPI(title="cve_risk_tier_severity_matrix")
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False)

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestSession() as db:
        s1 = McpServerRegistry(
            server_id="srv-1",
            name="server one",
            risk_tier="tier_a",
            url="http://example.com/1",
        )
        s2 = McpServerRegistry(
            server_id="srv-2",
            name="server two",
            risk_tier="tier_b",
            url="http://example.com/2",
        )
        db.add_all([s1, s2])
        db.flush()

        a1 = VulnAdvisory(
            id="adv-1",
            package="pkg1",
            ecosystem="npm",
            severity="high",
            summary="high severity advisory",
            source_url="http://example.com/adv1",
            feed="seed",
            content_hash="h1",
        )
        a2 = VulnAdvisory(
            id="adv-2",
            package="pkg2",
            ecosystem="npm",
            severity="critical",
            summary="critical severity advisory",
            source_url="http://example.com/adv2",
            feed="seed",
            content_hash="h2",
        )
        db.add_all([a1, a2])
        db.flush()

        l1 = VulnLink(
            advisory_id="adv-1",
            server_id="srv-1",
            match_basis="direct",
            match_value="pkg1",
            match_confidence=0.9,
        )
        l2 = VulnLink(
            advisory_id="adv-1",
            server_id="srv-2",
            match_basis="direct",
            match_value="pkg1",
            match_confidence=0.9,
        )
        l3 = VulnLink(
            advisory_id="adv-2",
            server_id="srv-1",
            match_basis="direct",
            match_value="pkg2",
            match_confidence=0.9,
        )
        db.add_all([l1, l2, l3])
        db.commit()

    client = TestClient(test_app)
    resp = client.get("/api/cve/risk-tier-severity-matrix")
    assert resp.status_code == 200, resp.status_code

    body = resp.json()
    rows = body["rows"]

    found = False
    for row in rows:
        nonzero_tiers = sum(1 for v in row["tiers"].values() if v > 0)
        if nonzero_tiers >= 2 and sum(row["tiers"].values()) > 0:
            found = True
            break

    assert found, f"need a row with >=2 non-zero tiers: {rows}"
    print("PASS")