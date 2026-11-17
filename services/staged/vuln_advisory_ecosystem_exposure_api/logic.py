from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["vuln-advisory-ecosystem-exposure"])


class EcosystemExposure(BaseModel):
    ecosystem: str
    advisory_count: int
    linked_server_count: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    max_published_at: Optional[str]


class EcosystemExposureResponse(BaseModel):
    total_advisories: int
    total_links: int
    severity_unknown_count: int
    ecosystems: list[EcosystemExposure]


def _iso_timestamp(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).isoformat()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return value
    return str(value)


@router.get(
    "/vuln/ecosystems",
    response_model=EcosystemExposureResponse,
    summary="Summarise advisory exposure by ecosystem",
)
def get_ecosystem_exposure(
    session: Session = Depends(get_session),
) -> EcosystemExposureResponse:
    total_advisories = int(
        session.execute(select(func.count(VulnAdvisory.id))).scalar_one()
    )
    total_links = int(
        session.execute(
            select(func.count(VulnLink.id))
            .select_from(VulnAdvisory)
            .outerjoin(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
        ).scalar_one()
    )

    severity = func.upper(func.trim(VulnAdvisory.severity))
    statement = (
        select(
            VulnAdvisory.ecosystem,
            func.count(distinct(VulnAdvisory.id)).label("advisory_count"),
            func.count(distinct(VulnLink.server_id)).label("linked_server_count"),
            func.count(
                distinct(
                    case((severity == "CRITICAL", VulnAdvisory.id), else_=None)
                )
            ).label("critical_count"),
            func.count(
                distinct(case((severity == "HIGH", VulnAdvisory.id), else_=None))
            ).label("high_count"),
            func.count(
                distinct(case((severity == "MEDIUM", VulnAdvisory.id), else_=None))
            ).label("medium_count"),
            func.count(
                distinct(case((severity == "LOW", VulnAdvisory.id), else_=None))
            ).label("low_count"),
            func.max(VulnAdvisory.published_at).label("max_published_at"),
        )
        .select_from(VulnAdvisory)
        .outerjoin(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
        .group_by(VulnAdvisory.ecosystem)
    )

    rows = session.execute(statement).mappings().all()
    ecosystems: list[EcosystemExposure] = []
    known_severity_count = 0

    for row in rows:
        critical_count = int(row["critical_count"] or 0)
        high_count = int(row["high_count"] or 0)
        medium_count = int(row["medium_count"] or 0)
        low_count = int(row["low_count"] or 0)
        known_severity_count += (
            critical_count + high_count + medium_count + low_count
        )

        ecosystems.append(
            EcosystemExposure(
                ecosystem="" if row["ecosystem"] is None else str(row["ecosystem"]),
                advisory_count=int(row["advisory_count"] or 0),
                linked_server_count=int(row["linked_server_count"] or 0),
                critical_count=critical_count,
                high_count=high_count,
                medium_count=medium_count,
                low_count=low_count,
                max_published_at=_iso_timestamp(row["max_published_at"]),
            )
        )

    ecosystems.sort(key=lambda item: (-item.advisory_count, item.ecosystem))

    return EcosystemExposureResponse(
        total_advisories=total_advisories,
        total_links=total_links,
        severity_unknown_count=max(total_advisories - known_severity_count, 0),
        ecosystems=ecosystems,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(test_engine)
    VulnLink.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id=1,
                    feed="test-feed",
                    summary="Critical advisory",
                    severity="CRITICAL",
                    ecosystem="npm",
                    package="example-package",
                    affected_ranges=["<2.0.0"],
                    aliases=["example-alias"],
                    source_url="https://example.test/advisories/1",
                    published_at=datetime(2025, 1, 1, tzinfo=None),
                    fetched_at=datetime(2025, 1, 2, tzinfo=None),
                    identities={"cve": ["CVE-2025-0001"]},
                    content_hash="test-hash-1",
                ),
                VulnAdvisory(
                    id=2,
                    feed="test-feed",
                    summary="High advisory",
                    severity="high",
                    ecosystem="pypi",
                    package="example-pypi-package",
                    affected_ranges=["<1.5.0"],
                    aliases=[],
                    source_url="https://example.test/advisories/2",
                    published_at=datetime(2025, 1, 3, tzinfo=None),
                    fetched_at=datetime(2025, 1, 4, tzinfo=None),
                    identities={},
                    content_hash="test-hash-2",
                ),
                VulnAdvisory(
                    id=3,
                    feed="test-feed",
                    summary="Unknown-severity advisory",
                    severity="UNKNOWN",
                    ecosystem="go",
                    package="example-go-package",
                    affected_ranges=[],
                    aliases=[],
                    source_url="https://example.test/advisories/3",
                    published_at=datetime(2025, 1, 5, tzinfo=None),
                    fetched_at=datetime(2025, 1, 6, tzinfo=None),
                    identities={},
                    content_hash="test-hash-3",
                ),
            ]
        )
        session.add_all(
            [
                VulnLink(
                    id=1,
                    advisory_id=1,
                    server_id="server-1",
                    match_basis="package",
                    match_value="example-package",
                    match_confidence=1.0,
                    linked_at=datetime(2025, 1, 2, tzinfo=None),
                ),
                VulnLink(
                    id=2,
                    advisory_id=1,
                    server_id="server-2",
                    match_basis="package",
                    match_value="example-package",
                    match_confidence=1.0,
                    linked_at=datetime(2025, 1, 2, tzinfo=None),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/ecosystems")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_advisories"] == 3
        assert payload["total_links"] == 2
        assert payload["severity_unknown_count"] == 1
        linked_ecosystems = [
            item
            for item in payload["ecosystems"]
            if item["linked_server_count"] == 2
        ]
        assert len(linked_ecosystems) == 1
        assert linked_ecosystems[0]["critical_count"] == 1

    print("PASS")