from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org

from .logic import get_api_key_age_audit

router = APIRouter(prefix="/api", tags=["api_key_age_audit"])


@router.get("/api-keys/age-audit")
def api_key_age_audit(
    org_id: str | None = None,
    max_age_days: int = Query(default=90, ge=0),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_api_key_age_audit(session, org_id, max_age_days)


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(bind=test_engine)
    ApiKey.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_session = TestSession()

    seeded_at = datetime.now(timezone.utc)
    age_days = (5, 20, 100, 200)
    test_session.add_all(
        [
            Org(id="org-10", name="Organization 10"),
            Org(id="org-20", name="Organization 20"),
        ]
    )
    test_session.add_all(
        [
            ApiKey(
                id=f"key-{index}",
                org_id="org-10" if index < 3 else "org-20",
                key_hash=f"test-hash-{index}",
                label=f"test-key-{index}",
                created_at=seeded_at - timedelta(days=age),
            )
            for index, age in enumerate(age_days, start=1)
        ]
    )
    test_session.commit()

    def override_get_session():
        yield test_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/api-keys/age-audit",
            params={"max_age_days": 90},
        )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    payload = response.json()
    expected_stale = sum(age > 90 for age in age_days)
    assert payload["total_keys"] == len(age_days)
    assert payload["stale_count"] == expected_stale
    assert sum(band["count"] for band in payload["bands"]) == payload["total_keys"]
    assert all("key_hash" not in key for key in payload["stale_keys"])

    print("PASS")