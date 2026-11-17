from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as SQLAlchemySession, sessionmaker

from app.db import get_session
from app.models import VulnAdvisory


class EcosystemIdentityCoverage(BaseModel):
    ecosystem: str
    total: int
    with_identities: int


class IdentityCoverageResponse(BaseModel):
    total_advisories: int
    with_identities: int
    with_aliases: int
    identity_coverage_ratio: float
    ecosystems: List[EcosystemIdentityCoverage]


def _has_json_values(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) > 0


def calculate_identity_coverage(session: SQLAlchemySession) -> IdentityCoverageResponse:
    rows = session.execute(
        select(
            VulnAdvisory.ecosystem,
            VulnAdvisory.identities,
            VulnAdvisory.aliases,
        )
    ).all()

    total_advisories = len(rows)
    with_identities = 0
    with_aliases = 0
    ecosystem_counts: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"total": 0, "with_identities": 0}
    )

    for ecosystem, identities, aliases in rows:
        ecosystem_name = ecosystem if ecosystem is not None else ""
        has_identities = _has_json_values(identities)
        has_aliases = _has_json_values(aliases)

        if has_identities:
            with_identities += 1
        if has_aliases:
            with_aliases += 1

        ecosystem_counts[ecosystem_name]["total"] += 1
        if has_identities:
            ecosystem_counts[ecosystem_name]["with_identities"] += 1

    ecosystems = [
        EcosystemIdentityCoverage(
            ecosystem=ecosystem,
            total=counts["total"],
            with_identities=counts["with_identities"],
        )
        for ecosystem, counts in sorted(
            ecosystem_counts.items(),
            key=lambda item: (-item[1]["total"], item[0]),
        )
    ]

    identity_coverage_ratio = (
        round(with_identities / total_advisories, 4) if total_advisories else 0.0
    )

    return IdentityCoverageResponse(
        total_advisories=total_advisories,
        with_identities=with_identities,
        with_aliases=with_aliases,
        identity_coverage_ratio=identity_coverage_ratio,
        ecosystems=ecosystems,
    )


router = APIRouter(prefix="/api")


@router.get(
    "/vuln-advisories/identity-coverage",
    response_model=IdentityCoverageResponse,
)
def get_identity_coverage(
    session: SQLAlchemySession = Depends(get_session),
) -> IdentityCoverageResponse:
    return calculate_identity_coverage(session)


app = FastAPI()
app.include_router(router)


def _run_self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(bind=engine)
    local_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        with local_session_factory() as session:
            yield session

    local_app = FastAPI()
    local_app.include_router(router)
    local_app.dependency_overrides[get_session] = override_get_session

    now = datetime.utcnow()
    with local_session_factory() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id=1,
                    feed="test",
                    summary="advisory one",
                    severity="high",
                    ecosystem="npm",
                    package="example-one",
                    affected_ranges=[],
                    aliases=["GHSA-example-one"],
                    source_url="https://example.invalid/one",
                    published_at=now,
                    fetched_at=now,
                    identities=["CVE-2026-0001"],
                    content_hash="coverage-test-1",
                ),
                VulnAdvisory(
                    id=2,
                    feed="test",
                    summary="advisory two",
                    severity="medium",
                    ecosystem="pypi",
                    package="example-two",
                    affected_ranges=[],
                    aliases=[],
                    source_url="https://example.invalid/two",
                    published_at=now,
                    fetched_at=now,
                    identities=["CVE-2026-0002"],
                    content_hash="coverage-test-2",
                ),
                VulnAdvisory(
                    id=3,
                    feed="test",
                    summary="advisory three",
                    severity="low",
                    ecosystem="npm",
                    package="example-three",
                    affected_ranges=[],
                    aliases=["GHSA-example-three"],
                    source_url="https://example.invalid/three",
                    published_at=now,
                    fetched_at=now,
                    identities=[],
                    content_hash="coverage-test-3",
                ),
                VulnAdvisory(
                    id=4,
                    feed="test",
                    summary="advisory four",
                    severity="low",
                    ecosystem="pypi",
                    package="example-four",
                    affected_ranges=[],
                    aliases=None,
                    source_url="https://example.invalid/four",
                    published_at=now,
                    fetched_at=now,
                    identities=None,
                    content_hash="coverage-test-4",
                ),
            ]
        )
        session.commit()

    response = TestClient(local_app).get("/api/vuln-advisories/identity-coverage")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_advisories"] == 4
    assert payload["with_identities"] == 2
    assert payload["with_aliases"] == 2
    assert payload["identity_coverage_ratio"] == 0.5
    assert len(payload["ecosystems"]) == 2
    assert payload["ecosystems"] == [
        {"ecosystem": "npm", "total": 2, "with_identities": 1},
        {"ecosystem": "pypi", "total": 2, "with_identities": 1},
    ]
    print("PASS")


if __name__ == "__main__":
    _run_self_test()