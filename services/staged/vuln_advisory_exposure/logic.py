"""logic.py -- vulnerability advisory exposure data/computation layer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


SEVERITY_ORDER = ("critical", "high", "medium", "low", "unknown")


class AdvisoryExposure(BaseModel):
    advisory_id: int
    feed: Optional[str]
    severity: str
    ecosystem: Optional[str]
    package: Optional[str]
    summary: Optional[str]
    match_basis: str
    match_value: str
    match_confidence: float
    linked_at: datetime
    published_at: Optional[datetime]
    source_url: str


class ServerExposure(BaseModel):
    server_id: str
    advisory_count: int
    severity_counts: Dict[str, int]
    max_match_confidence: Optional[float]
    advisories: List[AdvisoryExposure]


class ServerExposureSummary(BaseModel):
    server_id: str
    advisory_count: int
    severity_counts: Dict[str, int]
    top_severity: str


class ExposureSummary(BaseModel):
    generated_at: datetime
    servers: List[ServerExposureSummary]


def _empty_severity_counts() -> Dict[str, int]:
    return {severity: 0 for severity in SEVERITY_ORDER}


def _normalize_severity(value: Optional[str]) -> str:
    severity = (value or "").strip().lower()
    return severity if severity in SEVERITY_ORDER[:-1] else "unknown"


def _confidence(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _exposure_rows(db: Session, server_id: str):
    return db.execute(
        select(VulnLink, VulnAdvisory)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .where(VulnLink.server_id == server_id)
        .order_by(
            VulnAdvisory.published_at.desc(),
            VulnAdvisory.id.asc(),
        )
    ).all()


def _build_server_exposure(
    db: Session,
    server_id: str,
) -> ServerExposure:
    severity_counts = _empty_severity_counts()
    advisories: List[AdvisoryExposure] = []
    confidences: List[float] = []

    for link, advisory in _exposure_rows(db, server_id):
        severity = _normalize_severity(advisory.severity)
        confidence = _confidence(link.match_confidence)
        severity_counts[severity] += 1
        confidences.append(confidence)
        advisories.append(
            AdvisoryExposure(
                advisory_id=advisory.id,
                feed=advisory.feed,
                severity=severity,
                ecosystem=advisory.ecosystem,
                package=advisory.package,
                summary=advisory.summary,
                match_basis=link.match_basis,
                match_value=link.match_value,
                match_confidence=confidence,
                linked_at=link.linked_at,
                published_at=advisory.published_at,
                source_url=advisory.source_url,
            )
        )

    return ServerExposure(
        server_id=server_id,
        advisory_count=len(advisories),
        severity_counts=severity_counts,
        max_match_confidence=max(confidences) if confidences else None,
        advisories=advisories,
    )


def get_vuln_exposure(db: Session, server_id: str) -> ServerExposure:
    """Return advisory exposure for one server."""
    return _build_server_exposure(db, server_id)


def get_exposure(db: Session, server_id: str) -> ServerExposure:
    """Compatibility alias for callers using the shorter function name."""
    return get_vuln_exposure(db, server_id)


def list_vuln_exposure(db: Session, limit: int = 50) -> ExposureSummary:
    """Return a summary of servers with linked vulnerability advisories."""
    server_ids = db.execute(
        select(VulnLink.server_id)
        .distinct()
        .order_by(VulnLink.server_id.asc())
        .limit(limit)
    ).scalars().all()

    servers: List[ServerExposureSummary] = []
    for server_id in server_ids:
        exposure = _build_server_exposure(db, server_id)
        top_severity = next(
            (
                severity
                for severity in SEVERITY_ORDER
                if exposure.severity_counts.get(severity, 0) > 0
            ),
            "unknown",
        )
        servers.append(
            ServerExposureSummary(
                server_id=server_id,
                advisory_count=exposure.advisory_count,
                severity_counts=exposure.severity_counts,
                top_severity=top_severity,
            )
        )

    return ExposureSummary(
        generated_at=datetime.now(timezone.utc),
        servers=servers,
    )


router = APIRouter(prefix="/api")


@router.get("/vuln/exposure", response_model=ExposureSummary)
def vulnerability_exposure_summary(
    limit: int = Query(default=50, ge=1),
    db: Session = Depends(get_session),
) -> ExposureSummary:
    return list_vuln_exposure(db, limit)


@router.get("/vuln/exposure/{server_id}", response_model=ServerExposure)
def vulnerability_exposure_for_server(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerExposure:
    return get_vuln_exposure(db, server_id)


def create_app() -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    return application


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(bind=engine, checkfirst=True)
    VulnLink.__table__.create(bind=engine, checkfirst=True)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    with TestingSession() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id=1,
                    feed="test-feed",
                    summary="A high-severity advisory",
                    severity="HIGH",
                    ecosystem="python",
                    package="example-package",
                    affected_ranges="<1.0",
                    aliases="CVE-TEST-0001",
                    source_url="https://example.invalid/advisory/1",
                    published_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                    fetched_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
                    identities="{}",
                    content_hash="hash-1",
                ),
                VulnAdvisory(
                    id=2,
                    feed="test-feed",
                    summary="A medium-severity advisory",
                    severity="MEDIUM",
                    ecosystem="python",
                    package="example-package",
                    affected_ranges="<2.0",
                    aliases="CVE-TEST-0002",
                    source_url="https://example.invalid/advisory/2",
                    published_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    fetched_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
                    identities="{}",
                    content_hash="hash-2",
                ),
                VulnLink(
                    id=1,
                    advisory_id=1,
                    server_id="server_a",
                    match_basis="package",
                    match_value="example-package",
                    match_confidence=0.95,
                    linked_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
                ),
                VulnLink(
                    id=2,
                    advisory_id=2,
                    server_id="server_a",
                    match_basis="package",
                    match_value="example-package",
                    match_confidence=0.85,
                    linked_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/exposure/server_a")
        assert response.status_code == 200
        payload = response.json()
        assert payload["advisory_count"] == 2
        assert payload["severity_counts"]["high"] == 1

        response = client.get("/api/vuln/exposure/server_b")
        assert response.status_code == 200
        assert response.json()["advisory_count"] == 0
        assert response.json()["advisories"] == []

    print("PASS")