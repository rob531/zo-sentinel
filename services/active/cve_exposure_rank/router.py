# deps: fastapi, sqlalchemy, pydantic
"""CVE Exposure Rank Service - ranks servers by CVE exposure.

Returns servers ordered by weighted CVE severity score, joining
VulnLink → VulnAdvisory → McpServerRegistry from app Postgres.
"""
from __future__ import annotations

from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Base, McpServerRegistry, VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["cve_exposure_rank"])


class ServerCVERank(BaseModel):
    server_id: str
    name: Optional[str]
    cve_count: int
    weighted_score: int
    top_severity: Optional[str]

    model_config = {"from_attributes": True}


class CVEXposureRankResponse(BaseModel):
    ranked: List[ServerCVERank]


# Severity weight mapping
_SEVERITY_WEIGHTS = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1}
_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}


def _severity_weight(severity: Optional[str]) -> int:
    return _SEVERITY_WEIGHTS.get(severity or "", 0)


def _severity_order(severity: Optional[str]) -> int:
    return _SEVERITY_ORDER.get(severity or "", 99)


@router.get("/cve-exposure-rank", response_model=CVEXposureRankResponse)
def get_cve_exposure_rank(
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_session),
) -> CVEXposureRankResponse:
    """Return servers ranked by CVE exposure weighted score."""
    # Build weight expressions
    weight_expr = case(
        (VulnAdvisory.severity == "CRITICAL", 3),
        (VulnAdvisory.severity == "HIGH", 2),
        (VulnAdvisory.severity == "MEDIUM", 1),
        else_=0,
    )
    order_expr = case(
        (VulnAdvisory.severity == "CRITICAL", 0),
        (VulnAdvisory.severity == "HIGH", 1),
        (VulnAdvisory.severity == "MEDIUM", 2),
        else_=99,
    )

    # Aggregate per server
    rows = (
        db.query(
            VulnLink.server_id,
            func.count(VulnAdvisory.id).label("cve_count"),
            func.sum(weight_expr).label("weighted_score"),
            func.min(order_expr).label("top_severity_order"),
        )
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .group_by(VulnLink.server_id)
        .subquery()
    )

    # Join with server registry for name
    results = (
        db.query(
            rows.c.server_id,
            McpServerRegistry.name,
            rows.c.cve_count,
            rows.c.weighted_score,
            rows.c.top_severity_order,
        )
        .outerjoin(McpServerRegistry, rows.c.server_id == McpServerRegistry.server_id)
        .order_by(rows.c.weighted_score.desc().nullslast(), rows.c.cve_count.desc())
        .limit(limit)
        .all()
    )

    severity_map = {0: "CRITICAL", 1: "HIGH", 2: "MEDIUM"}
    ranked = [
        ServerCVERank(
            server_id=row.server_id,
            name=row.name,
            cve_count=row.cve_count,
            weighted_score=row.weighted_score or 0,
            top_severity=severity_map.get(row.top_severity_order, "LOW"),
        )
        for row in results
    ]

    return CVEXposureRankResponse(ranked=ranked)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        db.execute(
            """
            INSERT INTO mcp_server_registry (server_id, name, risk_tier, url, description)
            VALUES
                ('srv1', 'Server-Alpha', 'HIGH', 'https://alpha.example', 'Alpha server'),
                ('srv2', 'Server-Beta', 'MEDIUM', 'https://beta.example', 'Beta server'),
                ('srv3', 'Server-Gamma', 'CRITICAL', 'https://gamma.example', 'Gamma server');
            """
        )
        db.execute(
            """
            INSERT INTO vuln_advisories (id, feed, summary, severity, ecosystem, package, source_url)
            VALUES
                ('CVE-2024-0001', 'nvd', 'Critical RCE', 'CRITICAL', 'npm', 'evil-pkg', 'https://nvd.example/1'),
                ('CVE-2024-0002', 'nvd', 'Critical XSS', 'CRITICAL', 'npm', 'xss-pkg', 'https://nvd.example/2'),
                ('CVE-2024-0003', 'ghsa', 'High Priv Esc', 'HIGH', 'PyPI', 'priv-pkg', 'https://ghsa.example/3'),
                ('CVE-2024-0004', 'nvd', 'Medium DoS', 'MEDIUM', 'npm', 'dos-pkg', 'https://nvd.example/4'),
                ('CVE-2024-0005', 'ghsa', 'High SSRF', 'HIGH', 'PyPI', 'ssrf-pkg', 'https://ghsa.example/5');
            """
        )
        db.execute(
            """
            INSERT INTO vuln_links (advisory_id, server_id, match_basis, match_value, match_confidence)
            VALUES
                ('CVE-2024-0001', 'srv1', 'package_exact', 'evil-pkg', 1.0),
                ('CVE-2024-0003', 'srv1', 'package_exact', 'priv-pkg', 0.95),
                ('CVE-2024-0005', 'srv1', 'package_exact', 'ssrf-pkg', 0.90),
                ('CVE-2024-0004', 'srv2', 'package_exact', 'dos-pkg', 1.0),
                ('CVE-2024-0002', 'srv3', 'package_exact', 'xss-pkg', 1.0);
            """
        )
        db.commit()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    client = TestClient(app)

    resp = client.get("/api/cve-exposure-rank?limit=10")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    data = resp.json()["ranked"]
    assert len(data) == 3, f"Expected 3 servers, got {len(data)}"

    # srv1: CRITICAL(3)+HIGH(2)+HIGH(2) = 7, top=CRITICAL
    # srv3: CRITICAL(3) = 3, top=CRITICAL
    # srv2: MEDIUM(1) = 1, top=MEDIUM
    assert data[0]["server_id"] == "srv1"
    assert data[0]["cve_count"] == 3
    assert data[0]["weighted_score"] == 7
    assert data[0]["top_severity"] == "CRITICAL"

    assert data[1]["server_id"] == "srv3"
    assert data[1]["cve_count"] == 1
    assert data[1]["weighted_score"] == 3
    assert data[1]["top_severity"] == "CRITICAL"

    assert data[2]["server_id"] == "srv2"
    assert data[2]["cve_count"] == 1
    assert data[2]["weighted_score"] == 1
    assert data[2]["top_severity"] == "MEDIUM"

    print("PASS")
