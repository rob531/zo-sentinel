from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

from .logic import get_top_packages

router = APIRouter()


class EcosystemAdvisoryCount(BaseModel):
    ecosystem: str
    count: int


class PackageAdvisoryCount(BaseModel):
    package: str
    ecosystem: str
    count: int
    by_severity: Dict[str, int]
    latest_published_at: Optional[datetime]


class TopPackagesResponse(BaseModel):
    total_advisories: int
    ecosystems: List[EcosystemAdvisoryCount]
    packages: List[PackageAdvisoryCount]


@router.get(
    "/vuln/advisories/top-packages",
    response_model=TopPackagesResponse,
)
def top_packages(
    limit: int = Query(default=20, ge=1, le=200),
    session: Session = Depends(get_session),
) -> TopPackagesResponse:
    return get_top_packages(session, limit=limit)


if __name__ == "__main__":
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
    testing_session = sessionmaker(bind=engine)()

    now = datetime.now(timezone.utc)
    testing_session.add_all(
        [
            VulnAdvisory(
                id=str(uuid4()),
                feed="test-feed",
                summary="lodash advisory one",
                severity="critical",
                ecosystem="npm",
                package="lodash",
                affected_ranges=["<4.17.21"],
                aliases=[],
                source_url="https://example.test/lodash-1",
                published_at=now,
                fetched_at=now,
                identities=[],
                content_hash="lodash-1",
            ),
            VulnAdvisory(
                id=str(uuid4()),
                feed="test-feed",
                summary="lodash advisory two",
                severity="high",
                ecosystem="npm",
                package="lodash",
                affected_ranges=["<4.17.22"],
                aliases=[],
                source_url="https://example.test/lodash-2",
                published_at=now,
                fetched_at=now,
                identities=[],
                content_hash="lodash-2",
            ),
            VulnAdvisory(
                id=str(uuid4()),
                feed="test-feed",
                summary="requests advisory",
                severity="medium",
                ecosystem="npm",
                package="requests",
                affected_ranges=["<2.32.0"],
                aliases=[],
                source_url="https://example.test/requests-1",
                published_at=now,
                fetched_at=now,
                identities=[],
                content_hash="requests-1",
            ),
            VulnAdvisory(
                id=str(uuid4()),
                feed="test-feed",
                summary="urllib3 advisory",
                severity="low",
                ecosystem="pypi",
                package="urllib3",
                affected_ranges=["<2.2.0"],
                aliases=[],
                source_url="https://example.test/urllib3-1",
                published_at=now,
                fetched_at=now,
                identities=[],
                content_hash="urllib3-1",
            ),
        ]
    )
    testing_session.commit()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = lambda: testing_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/vuln/advisories/top-packages")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total_advisories"] == 4, payload
            assert len(payload["ecosystems"]) == 2, payload

            lodash = next(
                package
                for package in payload["packages"]
                if package["package"] == "lodash"
            )
            assert lodash["count"] == 2, payload
            assert lodash["by_severity"]["critical"] == 1, payload
            assert lodash["by_severity"]["high"] == 1, payload

            print("PASS")
    finally:
        testing_session.close()
        engine.dispose()