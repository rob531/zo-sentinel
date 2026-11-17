from datetime import datetime, timezone
from typing import Iterator, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


class ApiKeyInventoryItem(BaseModel):
    id: str
    org_id: str
    org_name: Optional[str]
    label: Optional[str]
    key_hash_prefix: str
    created_at: str
    age_days: int


class ApiKeyInventoryResponse(BaseModel):
    count: int
    keys: list[ApiKeyInventoryItem]
    orgs_without_keys: int


router = APIRouter()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _id_value(value) -> str:
    return str(value)


def get_api_key_inventory(
    session: Session = Depends(get_session),
    org_id: Optional[str] = None,
    include_org: bool = True,
) -> ApiKeyInventoryResponse:
    statement = (
        select(
            ApiKey.id,
            ApiKey.org_id,
            ApiKey.label,
            ApiKey.key_hash,
            ApiKey.created_at,
            Org.name,
        )
        .outerjoin(Org, ApiKey.org_id == Org.id)
        .order_by(ApiKey.created_at.desc(), ApiKey.id)
    )

    if org_id is not None:
        statement = statement.where(ApiKey.org_id == org_id)

    rows = session.execute(statement).all()
    now = datetime.now(timezone.utc)

    keys = []
    for row in rows:
        created_at = _as_utc(row.created_at)
        age_days = max(0, int((now - created_at).total_seconds() // 86400))
        keys.append(
            ApiKeyInventoryItem(
                id=_id_value(row.id),
                org_id=_id_value(row.org_id),
                org_name=row.name if include_org else None,
                label=row.label,
                key_hash_prefix=str(row.key_hash)[:8],
                created_at=created_at.isoformat(),
                age_days=age_days,
            )
        )

    org_without_key = ~exists(
        select(ApiKey.id).where(ApiKey.org_id == Org.id)
    )
    org_count_statement = select(func.count(Org.id)).where(org_without_key)
    if org_id is not None:
        org_count_statement = org_count_statement.where(Org.id == org_id)

    orgs_without_keys = int(
        session.execute(org_count_statement).scalar_one() or 0
    )

    return ApiKeyInventoryResponse(
        count=len(keys),
        keys=keys,
        orgs_without_keys=orgs_without_keys,
    )


@router.get(
    "/api/security/api-keys/inventory",
    response_model=ApiKeyInventoryResponse,
)
def api_key_inventory(
    org_id: Optional[str] = None,
    include_org: bool = True,
    session: Session = Depends(get_session),
) -> ApiKeyInventoryResponse:
    return get_api_key_inventory(
        session=session,
        org_id=org_id,
        include_org=include_org,
    )


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    Org.__table__.create(engine)
    ApiKey.__table__.create(engine)

    db = TestingSessionLocal()
    db.add_all(
        [
            Org(id="org-1", name="Organization One", created_at=datetime.now(timezone.utc)),
            Org(id="org-2", name="Organization Two", created_at=datetime.now(timezone.utc)),
            Org(id="org-3", name="Organization Without Keys", created_at=datetime.now(timezone.utc)),
            ApiKey(
                id="key-1",
                org_id="org-1",
                key_hash="abcdefgh123456789",
                label="Primary",
                created_at=datetime.now(timezone.utc),
            ),
            ApiKey(
                id="key-2",
                org_id="org-1",
                key_hash="12345678abcdefgh",
                label="Secondary",
                created_at=datetime.now(timezone.utc),
            ),
            ApiKey(
                id="key-3",
                org_id="org-2",
                key_hash="87654321hash-value",
                label="Reporting",
                created_at=datetime.now(timezone.utc),
            ),
        ]
    )
    db.commit()
    db.close()

    that_app = FastAPI()

    def override_session() -> Iterator[Session]:
        local_session = TestingSessionLocal()
        try:
            yield local_session
        finally:
            local_session.close()

    that_app.dependency_overrides[get_session] = override_session

    @that_app.get("/api/security/api-keys/inventory")
    def local_inventory(
        org_id: Optional[str] = None,
        include_org: bool = True,
        session: Session = Depends(get_session),
    ) -> dict:
        return get_api_key_inventory(
            session=session,
            org_id=org_id,
            include_org=include_org,
        ).model_dump()

    response = TestClient(that_app).get("/api/security/api-keys/inventory")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 3
    assert any(
        item["key_hash_prefix"] == "abcdefgh"
        for item in payload["keys"]
    )
    assert all(item["age_days"] >= 0 for item in payload["keys"])
    assert payload["orgs_without_keys"] == 1
    print("PASS")