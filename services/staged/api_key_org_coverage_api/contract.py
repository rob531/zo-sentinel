"""services/staged/api_key_org_coverage_api/contract.py

FastAPI contract for the “API key coverage per organisation” endpoint.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Real data layer imports – must not be stubbed.
from app.db import Base, get_session  # noqa: F401
from app.models import ApiKey, Org

router = APIRouter(prefix="/api")


class OrgCoverageItem(BaseModel):
    org_id: int = Field(..., description="Organisation identifier")
    org_name: str = Field(..., description="Organisation name")
    key_count: int = Field(..., description="Number of API keys belonging to the org")
    has_active_key: bool = Field(..., description="True if at least one key exists")
    first_key_at: Optional[datetime.datetime] = Field(
        None, description="Timestamp of the earliest key for the org"
    )


class CoverageSummary(BaseModel):
    total_orgs: int = Field(..., description="Total number of organisations")
    orgs_with_keys: int = Field(..., description="Organisations that have at least one key")
    orgs_without_keys: int = Field(..., description="Organisations that have no keys")


class CoverageResponse(BaseModel):
    orgs: List[OrgCoverageItem]
    summary: CoverageSummary


@router.get(
    "/orgs/api-key-coverage",
    response_model=CoverageResponse,
    summary="API key coverage per organisation",
)
def get_api_key_coverage(session: Session = Depends(get_session)) -> CoverageResponse:
    """
    Return per‑organisation API‑key coverage together with a fleet‑wide summary.
    """
    # Load all organisations.
    orgs = session.execute(select(Org)).scalars().all()

    # Load all keys, grouped by org_id.
    keys = session.execute(select(ApiKey)).scalars().all()
    keys_by_org: dict[int, List[ApiKey]] = defaultdict(list)
    for k in keys:
        keys_by_org[k.org_id].append(k)

    org_items: List[OrgCoverageItem] = []
    orgs_with_keys = 0

    for org in orgs:
        org_keys = keys_by_org.get(org.id, [])
        count = len(org_keys)
        has_key = count > 0
        first_at = min((k.created_at for k in org_keys), default=None)
        if has_key:
            orgs_with_keys += 1
        org_items.append(
            OrgCoverageItem(
                org_id=org.id,
                org_name=org.name,
                key_count=count,
                has_active_key=has_key,
                first_key_at=first_at,
            )
        )

    summary = CoverageSummary(
        total_orgs=len(orgs),
        orgs_with_keys=orgs_with_keys,
        orgs_without_keys=len(orgs) - orgs_with_keys,
    )
    return CoverageResponse(orgs=org_items, summary=summary)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.api_key_org_coverage_api.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real app DB for the test only)
    # ------------------------------------------------------------------- #
    TEST_DATABASE_URL = "sqlite://"
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Create tables using the real metadata.
    Base.metadata.create_all(bind=engine)

    # Dependency override that yields a session bound to the in‑memory DB.
    def get_test_session() -> Session:  # pragma: no cover
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Seed test data: 3 orgs, 2 api keys (org 1 and org 2 have keys)
    # ------------------------------------------------------------------- #
    now = datetime.datetime.utcnow()
    with TestingSessionLocal() as db:
        org1 = Org(id=1, name="Org One", created_at=now)
        org2 = Org(id=2, name="Org Two", created_at=now)
        org3 = Org(id=3, name="Org Three", created_at=now)
        db.add_all([org1, org2, org3])

        key1 = ApiKey(
            id=1,
            org_id=1,
            key_hash="hash1",
            label="key 1",
            created_at=now - datetime.timedelta(days=10),
        )
        key2 = ApiKey(
            id=2,
            org_id=2,
            key_hash="hash2",
            label="key 2",
            created_at=now - datetime.timedelta(days=5),
        )
        db.add_all([key1, key2])
        db.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with the router and the test session override.
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute the acceptance test.
    # ------------------------------------------------------------------- #
    resp = client.get("/api/orgs/api-key-coverage")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    summary = payload["summary"]
    assert summary["total_orgs"] == 3, "total_orgs should be 3"
    assert summary["orgs_without_keys"] == 1, "orgs_without_keys should be 1"

    print("PASS")
    sys.exit(0)