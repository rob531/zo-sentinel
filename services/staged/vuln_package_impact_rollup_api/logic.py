from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[3]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

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
    packages: list[PackageImpact]


router = APIRouter()


def _as_utc_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_vuln_package_impact_rollup(
    session: Session,
    limit: int = 25,
    ecosystem: Optional[str] = None,
) -> PackageImpactResponse:
    bounded_limit = max(1, min(int(limit), 200))
    statement = (
        select(
            VulnAdvisory.id,
            VulnAdvisory.package,
            VulnAdvisory.ecosystem,
            VulnAdvisory.severity,
            VulnAdvisory.published_at,
            VulnLink.server_id,
            VulnLink.match_confidence,
        )
        .join(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
    )
    if ecosystem is not None:
        statement = statement.where(
            func.lower(VulnAdvisory.ecosystem) == ecosystem.strip().lower()
        )

    rows = session.execute(statement).all()
    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(
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
            "latest_published_at": None,
        }
    )
    advisory_ids: set[str] = set()

    for row in rows:
        advisory_id = str(row.id)
        advisory_ids.add(advisory_id)
        package_key = (row.package or "", row.ecosystem or "")
        entry = grouped[package_key]
        entry["advisory_ids"].add(advisory_id)
        if row.server_id is not None:
            entry["server_ids"].add(str(row.server_id))

        severity = (row.severity or "").strip().lower()
        if severity in entry["severity_ids"]:
            entry["severity_ids"][severity].add(advisory_id)

        confidence = float(row.match_confidence or 0.0)
        entry["max_match_confidence"] = max(
            entry["max_match_confidence"], confidence
        )

        published_at = _as_utc_datetime(row.published_at)
        latest = entry["latest_published_at"]
        if published_at is not None and (latest is None or published_at > latest):
            entry["latest_published_at"] = published_at

    packages: list[PackageImpact] = []
    for (package, package_ecosystem), entry in grouped.items():
        severity_ids = entry["severity_ids"]
        latest_published_at = entry["latest_published_at"]
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
                latest_published_at=(
                    latest_published_at.isoformat() if latest_published_at else ""
                ),
            )
        )

    packages.sort(
        key=lambda item: (
            -item.linked_server_count,
            item.package,
            item.ecosystem,
        )
    )
    return PackageImpactResponse(
        total_advisories=len(advisory_ids),
        packages=packages[:bounded_limit],
    )


@router.get("/vuln/packages", response_model=PackageImpactResponse)
def get_package_impact_rollup(
    limit: int = Query(default=25, ge=1),
    ecosystem: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> PackageImpactResponse:
    return get_vuln_package_impact_rollup(
        session=session,
        limit=min(limit, 200),
        ecosystem=ecosystem,
    )


def _run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

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