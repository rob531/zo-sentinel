from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


class SeverityCounts(BaseModel):
    critical: int
    high: int
    medium: int
    low: int


class PackageImpact(BaseModel):
    package: str
    ecosystem: str
    advisory_count: int
    linked_server_count: int
    severities: SeverityCounts
    max_match_confidence: float
    latest_published_at: str


class PackageImpactResponse(BaseModel):
    total_advisories: int
    packages: List[PackageImpact]


router = APIRouter()


def _published_sort_value(value: object) -> float:
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or datetime.now().astimezone().tzinfo).timestamp()
    return 0.0


@router.get("/vuln/packages", response_model=PackageImpactResponse)
def get_package_impact_rollup(
    limit: int = Query(default=25, ge=1),
    ecosystem: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> PackageImpactResponse:
    limit = min(limit, 200)

    statement = select(VulnAdvisory, VulnLink).join(
        VulnLink, VulnLink.advisory_id == VulnAdvisory.id
    )
    if ecosystem is not None:
        statement = statement.where(
            func.lower(VulnAdvisory.ecosystem) == ecosystem.strip().lower()
        )

    rows = session.execute(statement).all()
    grouped: Dict[tuple, dict] = defaultdict(
        lambda: {
            "advisory_ids": set(),
            "server_ids": set(),
            "severity_ids": {
                "critical": set(),
                "high": set(),
                "medium": set(),
                "low": set(),
            },
            "max_match_confidence": 0.0,
            "published": [],
        }
    )
    advisory_ids = set()

    for advisory, link in rows:
        advisory_id = str(advisory.id)
        advisory_ids.add(advisory_id)
        key = (advisory.package or "", advisory.ecosystem or "")
        entry = grouped[key]
        entry["advisory_ids"].add(advisory_id)
        entry["server_ids"].add(link.server_id)

        severity = (advisory.severity or "").lower()
        if severity in entry["severity_ids"]:
            entry["severity_ids"][severity].add(advisory_id)

        confidence = float(link.match_confidence or 0.0)
        entry["max_match_confidence"] = max(
            entry["max_match_confidence"], confidence
        )
        if advisory.published_at is not None:
            entry["published"].append(advisory.published_at)

    packages = []
    for (package, package_ecosystem), entry in grouped.items():
        published_values = entry["published"]
        if published_values:
            latest = max(published_values, key=_published_sort_value)
            latest_published_at = (
                latest.isoformat() if isinstance(latest, datetime) else str(latest)
            )
        else:
            latest_published_at = ""

        severity_ids = entry["severity_ids"]
        packages.append(
            PackageImpact(
                package=package,
                ecosystem=package_ecosystem,
                advisory_count=len(entry["advisory_ids"]),
                linked_server_count=len(entry["server_ids"]),
                severities=SeverityCounts(
                    critical=len(severity_ids["critical"]),
                    high=len(severity_ids["high"]),
                    medium=len(severity_ids["medium"]),
                    low=len(severity_ids["low"]),
                ),
                max_match_confidence=float(entry["max_match_confidence"]),
                latest_published_at=latest_published_at,
            )
        )

    packages.sort(key=lambda item: (-item.linked_server_count, item.package))
    return PackageImpactResponse(
        total_advisories=len(advisory_ids),
        packages=packages[:limit],
    )


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    VulnAdvisory.__table__.create(bind=engine)
    VulnLink.__table__.create(bind=engine)

    with SessionLocal() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id="adv-requests-1",
                    feed="test",
                    summary="Test requests advisory",
                    severity="critical",
                    ecosystem="pypi",
                    package="requests",
                    affected_ranges=[{"introduced": "0"}],
                    aliases=["req"],
                    source_url="https://example.test/requests",
                    published_at=datetime(2024, 1, 2, 3, 4, 5),
                    fetched_at=datetime(2024, 1, 2, 4, 0, 0),
                    identities=["py:requests"],
                    content_hash="requests-hash",
                ),
                VulnAdvisory(
                    id="adv-lodash-1",
                    feed="test",
                    summary="Test lodash advisory",
                    severity="high",
                    ecosystem="npm",
                    package="lodash",
                    affected_ranges=[{"introduced": "0"}],
                    aliases=[],
                    source_url="https://example.test/lodash",
                    published_at=datetime(2024, 1, 3, 3, 4, 5),
                    fetched_at=datetime(2024, 1, 3, 4, 0, 0),
                    identities=["npm:lodash"],
                    content_hash="lodash-hash",
                ),
            ]
        )
        session.add_all(
            [
                VulnLink(
                    id=1,
                    advisory_id="adv-requests-1",
                    server_id="server-a",
                    match_basis="package",
                    match_value="requests",
                    match_confidence=0.99,
                    linked_at=datetime(2024, 1, 4),
                ),
                VulnLink(
                    id=2,
                    advisory_id="adv-requests-1",
                    server_id="server-b",
                    match_basis="package",
                    match_value="requests",
                    match_confidence=0.95,
                    linked_at=datetime(2024, 1, 5),
                ),
                VulnLink(
                    id=3,
                    advisory_id="adv-lodash-1",
                    server_id="server-a",
                    match_basis="package",
                    match_value="lodash",
                    match_confidence=0.90,
                    linked_at=datetime(2024, 1, 6),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/vuln/packages", params={"limit": 25})
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total_advisories"] == 2, payload
            assert payload["packages"][0]["package"] == "requests", payload
            assert payload["packages"][0]["linked_server_count"] == 2, payload
            assert payload["packages"][0]["advisory_count"] == 1, payload

            for package in payload["packages"]:
                assert set(package["severities"]) == {
                    "critical",
                    "high",
                    "medium",
                    "low",
                }, package
                assert isinstance(package["max_match_confidence"], float), package
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()