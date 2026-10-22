from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org

try:
    from .logic import get_api_key_rotation_audit as _logic_audit
except ImportError:
    try:
        from .logic import audit_api_key_rotation as _logic_audit
    except ImportError:
        try:
            from .logic import get_rotation_audit as _logic_audit
        except ImportError:
            _logic_audit = None


router = APIRouter(prefix="/api")


class AgeBandCount(BaseModel):
    age_band: str
    count: int


class OrganizationRotationAudit(BaseModel):
    org_id: str
    org_name: str
    total_keys: int
    rotation_due: int
    oldest_key_age_days: int


class ApiKeyRotationAuditResponse(BaseModel):
    age_bands: List[AgeBandCount]
    orgs: List[OrganizationRotationAudit]
    total_keys: int
    rotation_due_total: int
    generated_at: str


def _age_band(age_days: int) -> str:
    if age_days <= 30:
        return "0-30"
    if age_days <= 90:
        return "31-90"
    if age_days <= 180:
        return "91-180"
    if age_days <= 365:
        return "181-365"
    return "365+"


def _age_in_days(created_at: datetime, now: datetime) -> int:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)
    return max(0, (now - created_at).days)


def _calculate_rotation_audit(session: Session) -> Dict[str, object]:
    now = datetime.now(timezone.utc)
    statement = (
        select(
            ApiKey.id,
            ApiKey.org_id,
            ApiKey.created_at,
            Org.id,
            Org.name,
        )
        .select_from(ApiKey)
        .join(Org, ApiKey.org_id == Org.id)
    )

    band_order = ["0-30", "31-90", "91-180", "181-365", "365+"]
    band_counts: Dict[str, int] = {band: 0 for band in band_order}
    organizations: Dict[str, Dict[str, object]] = {}
    total_keys = 0
    rotation_due_total = 0

    for key_id, org_id, created_at, joined_org_id, org_name in session.execute(statement):
        del key_id
        age_days = _age_in_days(created_at, now)
        band = _age_band(age_days)
        band_counts[band] += 1
        total_keys += 1

        rotation_due = age_days > 90
        if rotation_due:
            rotation_due_total += 1

        org = organizations.setdefault(
            org_id,
            {
                "org_id": org_id,
                "org_name": org_name,
                "total_keys": 0,
                "rotation_due": 0,
                "oldest_key_age_days": 0,
            },
        )
        org["total_keys"] = int(org["total_keys"]) + 1
        if rotation_due:
            org["rotation_due"] = int(org["rotation_due"]) + 1
        org["oldest_key_age_days"] = max(
            int(org["oldest_key_age_days"]), age_days
        )
        del joined_org_id

    return {
        "age_bands": [
            {"age_band": band, "count": count}
            for band, count in band_counts.items()
            if count > 0
        ],
        "orgs": sorted(
            organizations.values(),
            key=lambda item: (str(item["org_name"]), str(item["org_id"])),
        ),
        "total_keys": total_keys,
        "rotation_due_total": rotation_due_total,
        "generated_at": now.isoformat(),
    }


@router.get(
    "/audit/api-keys/rotation",
    response_model=ApiKeyRotationAuditResponse,
)
def audit_api_key_rotation(
    session: Session = Depends(get_session),
) -> ApiKeyRotationAuditResponse:
    result: Optional[Dict[str, object]]
    if callable(_logic_audit):
        result = _logic_audit(session)
    else:
        result = _calculate_rotation_audit(session)
    return ApiKeyRotationAuditResponse.parse_obj(result)


if __name__ == "__main__":
    from datetime import timedelta

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
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                Org(id="org-1", name="Security Org", created_at=now),
                Org(id="org-2", name="Platform Org", created_at=now),
                ApiKey(
                    id="key-old",
                    org_id="org-1",
                    label="old-key",
                    key_hash="never-exposed-old-hash",
                    created_at=now - timedelta(days=200),
                ),
                ApiKey(
                    id="key-new",
                    org_id="org-1",
                    label="new-key",
                    key_hash="never-exposed-new-hash",
                    created_at=now - timedelta(days=10),
                ),
                ApiKey(
                    id="key-medium",
                    org_id="org-2",
                    label="medium-key",
                    key_hash="never-exposed-medium-hash",
                    created_at=now - timedelta(days=60),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/audit/api-keys/rotation")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_keys"] == 3
    assert payload["rotation_due_total"] == 1
    assert len(payload["age_bands"]) == 3
    assert "key_hash" not in response.text
    print("PASS")