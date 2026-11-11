from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

from .logic import get_vuln_ecosystem_distribution as _get_vuln_ecosystem_distribution

router = APIRouter(prefix="/api")


class SeverityCounts(BaseModel):
    critical: int
    high: int
    medium: int
    low: int
    unknown: int


class EcosystemCount(BaseModel):
    ecosystem: str
    count: int
    latest_published_at: str | None
    severities: SeverityCounts


class EcosystemDistributionResponse(BaseModel):
    total: int
    ecosystems: list[EcosystemCount]


@router.get(
    "/vuln/ecosystem",
    response_model=EcosystemDistributionResponse,
)
def get_vuln_ecosystem_distribution(
    session: Session = Depends(get_session),
) -> EcosystemDistributionResponse:
    result = _get_vuln_ecosystem_distribution(session)
    return EcosystemDistributionResponse(**result)


def _self_test() -> None:
    from datetime import datetime, timezone
    from uuid import uuid4

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
    VulnAdvisory.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    rows = [
        VulnAdvisory(
            id=str(uuid4()),
            feed="test-feed",
            summary="First Python advisory",
            severity="critical",
            ecosystem="python",
            package="package-one",
            affected_ranges="<1.0",
            aliases="alias-one",
            source_url="https://example.test/1",
            published_at=now,
            fetched_at=now,
            identities="identity-one",
            content_hash="hash-one",
        ),
        VulnAdvisory(
            id=str(uuid4()),
            feed="test-feed",
            summary="Second Python advisory",
            severity="high",
            ecosystem="python",
            package="package-two",
            affected_ranges="<2.0",
            aliases="alias-two",
            source_url="https://example.test/2",
            published_at=now,
            fetched_at=now,
            identities="identity-two",
            content_hash="hash-two",
        ),
        VulnAdvisory(
            id=str(uuid4()),
            feed="test-feed",
            summary="Node advisory",
            severity="medium",
            ecosystem="npm",
            package="package-three",
            affected_ranges="<3.0",
            aliases="alias-three",
            source_url="https://example.test/3",
            published_at=now,
            fetched_at=now,
            identities="identity-three",
            content_hash="hash-three",
        ),
        VulnAdvisory(
            id=str(uuid4()),
            feed="test-feed",
            summary="Advisory without an ecosystem",
            severity="unknown",
            ecosystem=None,
            package="package-four",
            affected_ranges="<4.0",
            aliases="alias-four",
            source_url="https://example.test/4",
            published_at=None,
            fetched_at=now,
            identities="identity-four",
            content_hash="hash-four",
        ),
        VulnAdvisory(
            id=str(uuid4()),
            feed="test-feed",
            summary="Low-severity Go advisory",
            severity="low",
            ecosystem="go",
            package="package-five",
            affected_ranges="<5.0",
            aliases="alias-five",
            source_url="https://example.test/5",
            published_at=now,
            fetched_at=now,
            identities="identity-five",
            content_hash="hash-five",
        ),
    ]

    with testing_session() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/vuln/ecosystem")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert payload["ecosystems"][0]["count"] == 2
    assert len(payload["ecosystems"]) == 4
    assert any(item["ecosystem"] == "unknown" and item["count"] == 1 for item in payload["ecosystems"])
    print("PASS")


if __name__ == "__main__":
    _self_test()