from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


def _exposure_query(
    severity: Optional[str] = None,
    min_confidence: Optional[float] = None,
):
    query = (
        select(
            VulnAdvisory.ecosystem,
            func.count(func.distinct(VulnAdvisory.id)).label("advisory_count"),
            func.count(func.distinct(VulnLink.id)).label("link_count"),
            func.count(func.distinct(VulnLink.server_id)).label("server_count"),
            func.avg(VulnLink.match_confidence).label("avg_match_confidence"),
        )
        .outerjoin(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
    )

    if severity is not None:
        query = query.where(VulnAdvisory.severity == severity)
    if min_confidence is not None:
        query = query.where(VulnLink.match_confidence >= min_confidence)

    return query.group_by(VulnAdvisory.ecosystem)


def _severity_query(
    severity: Optional[str] = None,
    min_confidence: Optional[float] = None,
):
    query = (
        select(
            VulnAdvisory.ecosystem,
            VulnAdvisory.severity,
            func.count(func.distinct(VulnAdvisory.id)).label("advisory_count"),
        )
        .outerjoin(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
    )

    if severity is not None:
        query = query.where(VulnAdvisory.severity == severity)
    if min_confidence is not None:
        query = query.where(VulnLink.match_confidence >= min_confidence)

    return query.group_by(VulnAdvisory.ecosystem, VulnAdvisory.severity)


def get_ecosystem_exposure(
    db: Session,
    severity: Optional[str] = None,
    min_confidence: Optional[float] = None,
) -> Dict[str, object]:
    """Return vulnerability exposure grouped by ecosystem and severity."""
    ecosystem_rows = db.execute(
        _exposure_query(severity=severity, min_confidence=min_confidence)
    ).all()
    severity_rows = db.execute(
        _severity_query(severity=severity, min_confidence=min_confidence)
    ).all()

    severity_counts: Dict[str, Dict[str, int]] = {}
    for ecosystem, row_severity, count in severity_rows:
        ecosystem_name = ecosystem or "unknown"
        severity_counts.setdefault(ecosystem_name, {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
        })
        if row_severity in severity_counts[ecosystem_name]:
            severity_counts[ecosystem_name][row_severity] = int(count)

    ecosystems: List[Dict[str, object]] = []
    for ecosystem, advisory_count, link_count, server_count, average in ecosystem_rows:
        ecosystem_name = ecosystem or "unknown"
        ecosystems.append({
            "ecosystem": ecosystem_name,
            "advisory_count": int(advisory_count or 0),
            "linked_server_count": int(server_count or 0),
            "severities": severity_counts.get(
                ecosystem_name,
                {"critical": 0, "high": 0, "medium": 0, "low": 0},
            ),
            "avg_match_confidence": float(average or 0.0),
        })

    ecosystems.sort(key=lambda item: item["ecosystem"])

    totals_row = db.execute(
        select(
            func.count(func.distinct(VulnAdvisory.id)),
            func.count(func.distinct(VulnLink.id)),
            func.count(func.distinct(VulnLink.server_id)),
        )
        .outerjoin(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
        .where(
            VulnAdvisory.severity == severity
            if severity is not None
            else VulnAdvisory.id.is_not(None)
        )
        .where(
            VulnLink.match_confidence >= min_confidence
            if min_confidence is not None
            else VulnLink.id.is_not(None) | VulnAdvisory.id.is_not(None)
        )
    ).one()

    return {
        "ecosystems": ecosystems,
        "totals": {
            "advisories": int(totals_row[0] or 0),
            "links": int(totals_row[1] or 0),
            "servers": int(totals_row[2] or 0),
        },
    }


ecosystem_exposure = get_ecosystem_exposure
get_exposure = get_ecosystem_exposure


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    VulnLink.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with SessionLocal() as db:
        db.add_all([
            VulnAdvisory(
                id=1,
                feed="test-feed",
                summary="npm advisory",
                severity="critical",
                ecosystem="npm",
                package="example-npm-package",
                affected_ranges="*",
                aliases="",
                source_url="https://example.test/npm",
                published_at=datetime.now(timezone.utc),
                fetched_at=datetime.now(timezone.utc),
                identities="",
                content_hash="npm-hash",
            ),
            VulnAdvisory(
                id=2,
                feed="test-feed",
                summary="pypi advisory",
                severity="high",
                ecosystem="pypi",
                package="example-pypi-package",
                affected_ranges="*",
                aliases="",
                source_url="https://example.test/pypi",
                published_at=datetime.now(timezone.utc),
                fetched_at=datetime.now(timezone.utc),
                identities="",
                content_hash="pypi-hash",
            ),
            VulnAdvisory(
                id=3,
                feed="test-feed",
                summary="go advisory",
                severity="medium",
                ecosystem="go",
                package="example-go-package",
                affected_ranges="*",
                aliases="",
                source_url="https://example.test/go",
                published_at=datetime.now(timezone.utc),
                fetched_at=datetime.now(timezone.utc),
                identities="",
                content_hash="go-hash",
            ),
            VulnLink(
                id=1,
                advisory_id=1,
                server_id="server-a",
                match_basis="package",
                match_value="example-npm-package",
                match_confidence=0.95,
                linked_at=datetime.now(timezone.utc),
            ),
            VulnLink(
                id=2,
                advisory_id=2,
                server_id="server-b",
                match_basis="package",
                match_value="example-pypi-package",
                match_confidence=0.90,
                linked_at=datetime.now(timezone.utc),
            ),
        ])
        db.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/vuln/ecosystem")
    def ecosystem_endpoint(
        db: Session = Depends(get_session),
        severity: Optional[str] = None,
        min_confidence: Optional[float] = None,
    ):
        return get_ecosystem_exposure(
            db,
            severity=severity,
            min_confidence=min_confidence,
        )

    response = TestClient(test_app).get("/api/vuln/ecosystem")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["ecosystems"]) == 3

    npm = next(item for item in payload["ecosystems"] if item["ecosystem"] == "npm")
    assert npm["advisory_count"] == 1
    assert npm["severities"]["critical"] == 1
    assert payload["totals"]["links"] == 2
    assert payload["totals"]["servers"] == 2

    print("PASS")