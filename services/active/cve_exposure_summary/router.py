# deps: fastapi, sqlalchemy, pydantic, requests
"""CVE Exposure Summary Service.

Returns a summary of CVE exposure: total advisories and links, grouped by
severity, plus the top critical advisories.
"""

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["cve_exposure_summary"])


class SeverityCount(BaseModel):
    count: int
    servers: int


class SeveritySummary(BaseModel):
    total_advisories: int
    total_links: int
    by_severity: Dict[str, SeverityCount]
    top_critical: List[Dict]


@router.get(
    "/cve/exposure-summary",
    response_model=SeveritySummary,
    name="cve_exposure_summary:get_summary",
)
def exposure_summary(db: Session = Depends(get_session)) -> SeveritySummary:
    """
    Return a summary of CVE exposure grouped by severity and a list of the
    top critical advisories.
    """
    total_advisories = db.query(func.count(VulnAdvisory.id)).scalar() or 0
    total_links = db.query(func.count(VulnLink.id)).scalar() or 0

    # Per-severity aggregation with server counts
    severity_agg = (
        db.query(
            VulnAdvisory.severity,
            func.count(VulnAdvisory.id).label("adv_cnt"),
            func.count(func.distinct(VulnLink.server_id)).label("srv_cnt"),
        )
        .outerjoin(VulnLink, VulnAdvisory.id == VulnLink.advisory_id)
        .group_by(VulnAdvisory.severity)
        .all()
    )
    severity_map = {}
    for row in severity_agg:
        if row.severity:
            severity_map[row.severity] = SeverityCount(
                count=row.adv_cnt or 0,
                servers=row.srv_cnt or 0,
            )

    # Ensure all expected keys exist
    for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"):
        if sev not in severity_map:
            severity_map[sev] = SeverityCount(count=0, servers=0)

    # Top 5 advisories by published_at desc
    top_advisories = (
        db.query(VulnAdvisory)
        .order_by(VulnAdvisory.published_at.desc().nullslast())
        .limit(5)
        .all()
    )

    top_critical: List[Dict] = []
    for adv in top_advisories:
        srv_cnt = (
            db.query(func.count(func.distinct(VulnLink.server_id)))
            .filter(VulnLink.advisory_id == adv.id)
            .scalar()
            or 0
        )
        published = adv.published_at
        top_critical.append(
            {
                "id": adv.id,
                "summary": adv.summary,
                "severity": adv.severity,
                "published_at": (
                    published.isoformat()
                    if isinstance(published, datetime)
                    else str(published) if published else None
                ),
                "affected_servers": srv_cnt,
            }
        )

    return SeveritySummary(
        total_advisories=total_advisories,
        total_links=total_links,
        by_severity=severity_map,
        top_critical=top_critical,
    )


if __name__ == "__main__":
    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        db.execute(
            text(
                """
            INSERT INTO vuln_advisories (id, feed, summary, severity, source_url, published_at)
            VALUES
                ('CVE-2023-0001', 'nvd', 'Critical vuln 1', 'CRITICAL', 'https://nvd.nist.gov/vuln/detail/CVE-2023-0001', '2023-01-01T00:00:00'),
                ('CVE-2023-0002', 'nvd', 'Critical vuln 2', 'CRITICAL', 'https://nvd.nist.gov/vuln/detail/CVE-2023-0002', '2023-01-02T00:00:00'),
                ('CVE-2023-0003', 'nvd', 'High vuln', 'HIGH', 'https://nvd.nist.gov/vuln/detail/CVE-2023-0003', '2023-01-03T00:00:00');
            """
            )
        )
        db.execute(
            text(
                """
            INSERT INTO vuln_links (advisory_id, server_id, match_basis, match_value, match_confidence)
            VALUES
                ('CVE-2023-0001', 'srv1', 'package_exact', 'pkg1', 1.0),
                ('CVE-2023-0001', 'srv2', 'package_exact', 'pkg1', 1.0),
                ('CVE-2023-0002', 'srv1', 'package_exact', 'pkg2', 1.0),
                ('CVE-2023-0003', 'srv2', 'package_exact', 'pkg3', 1.0);
            """
            )
        )
        db.commit()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    client = TestClient(app)
    resp = client.get("/api/cve/exposure-summary")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()

    assert "by_severity" in payload, "Missing 'by_severity' in response"
    assert "top_critical" in payload, "Missing 'top_critical' in response"
    assert isinstance(payload["top_critical"], list), "top_critical not a list"
    assert len(payload["top_critical"]) == 3, f"Expected 3 entries, got {len(payload['top_critical'])}"

    print("PASS")
