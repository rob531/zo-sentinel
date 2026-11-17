from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org

from .logic import get_api_key_age_report as build_api_key_age_report

router = APIRouter(prefix="/api", tags=["api-key-age-report"])


class ApiKeyAgeBucket(BaseModel):
    bucket: str
    count: int


class OrgApiKeyAgeBreakdown(BaseModel):
    org_id: str
    org_name: str | None
    total: int
    oldest_days: int
    newest_days: int


class ApiKeyAgeReportResponse(BaseModel):
    total_keys: int
    buckets: list[ApiKeyAgeBucket]
    orgs: list[OrgApiKeyAgeBreakdown]
    stale_over_90d: int


@router.get(
    "/keys/age-report",
    response_model=ApiKeyAgeReportResponse,
)
def get_api_key_age_report(
    db: Session = Depends(get_session),
) -> ApiKeyAgeReportResponse:
    return ApiKeyAgeReportResponse(**build_api_key_age_report(db))


def _run_self_test() -> None:
    from datetime import datetime, timedelta, timezone

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
    Org.__table__.create(test_engine)
    ApiKey.__table__.create(test_engine)
    TestingSessionLocal = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
    )

    seeded_ages = (1, 10, 40, 100, 400)
    base_time = datetime.now(timezone.utc).replace(tzinfo=None)

    with TestingSessionLocal() as seed_session:
        seed_session.add(Org(id="org-1", name="Example Org"))
        seed_session.add_all(
            [
                ApiKey(
                    id=f"key-{index}",
                    org_id="org-1",
                    key_hash=f"hash-{index}",
                    label=f"key-{index}",
                    created_at=base_time - timedelta(days=age_days),
                )
                for index, age_days in enumerate(seeded_ages, start=1)
            ]
        )
        seed_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/keys/age-report")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_keys"] == 5
        over_365 = next(
            bucket for bucket in payload["buckets"] if bucket["bucket"] == ">365"
        )
        assert over_365["count"] == sum(age_days > 365 for age_days in seeded_ages)
        assert all(bucket["count"] >= 0 for bucket in payload["buckets"])

    test_engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()