from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


router = APIRouter(prefix="/api", tags=["api-key-org-fanout"])


class ApiKeyOrgFanoutItem(BaseModel):
    org_id: str
    org_name: str
    key_count: int
    oldest_key_age_days: float
    newest_key_age_days: float


class ApiKeyOrgFanoutResponse(BaseModel):
    orgs: list[ApiKeyOrgFanoutItem]
    total_keys: int


def _age_days(created_at: Optional[datetime], now: datetime) -> float:
    if created_at is None:
        return 0.0
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)
    return max(0.0, (now - created_at).total_seconds() / 86400.0)


def get_api_key_org_fanout(
    session: Session,
    limit: int = 50,
    include_empty: bool = False,
) -> ApiKeyOrgFanoutResponse:
    now = datetime.now(timezone.utc)

    if include_empty:
        rows = session.execute(
            select(
                Org.id.label("org_id"),
                Org.name.label("org_name"),
                func.count(ApiKey.id).label("key_count"),
                func.min(ApiKey.created_at).label("oldest_created_at"),
                func.max(ApiKey.created_at).label("newest_created_at"),
            )
            .outerjoin(ApiKey, ApiKey.org_id == Org.id)
            .group_by(Org.id, Org.name)
        ).all()

        orphan_rows = session.execute(
            select(
                ApiKey.org_id.label("org_id"),
                func.count(ApiKey.id).label("key_count"),
                func.min(ApiKey.created_at).label("oldest_created_at"),
                func.max(ApiKey.created_at).label("newest_created_at"),
            )
            .outerjoin(Org, Org.id == ApiKey.org_id)
            .where(Org.id.is_(None))
            .group_by(ApiKey.org_id)
        ).all()

        groups = [
            {
                "org_id": str(row.org_id),
                "org_name": str(row.org_name or ""),
                "key_count": int(row.key_count),
                "oldest_key_age_days": _age_days(row.oldest_created_at, now),
                "newest_key_age_days": _age_days(row.newest_created_at, now),
            }
            for row in rows
        ]
        groups.extend(
            {
                "org_id": str(row.org_id),
                "org_name": "",
                "key_count": int(row.key_count),
                "oldest_key_age_days": _age_days(row.oldest_created_at, now),
                "newest_key_age_days": _age_days(row.newest_created_at, now),
            }
            for row in orphan_rows
        )
    else:
        rows = session.execute(
            select(
                ApiKey.org_id.label("org_id"),
                Org.name.label("org_name"),
                func.count(ApiKey.id).label("key_count"),
                func.min(ApiKey.created_at).label("oldest_created_at"),
                func.max(ApiKey.created_at).label("newest_created_at"),
            )
            .outerjoin(Org, Org.id == ApiKey.org_id)
            .group_by(ApiKey.org_id, Org.name)
        ).all()

        groups = [
            {
                "org_id": str(row.org_id),
                "org_name": str(row.org_name or ""),
                "key_count": int(row.key_count),
                "oldest_key_age_days": _age_days(row.oldest_created_at, now),
                "newest_key_age_days": _age_days(row.newest_created_at, now),
            }
            for row in rows
        ]

    groups.sort(
        key=lambda item: (
            -item["oldest_key_age_days"],
            -item["key_count"],
            item["org_id"],
        )
    )
    groups = groups[:limit]

    total_keys = int(session.execute(select(func.count(ApiKey.id))).scalar() or 0)
    return ApiKeyOrgFanoutResponse(orgs=groups, total_keys=total_keys)


@router.get("/api-key-org-fanout", response_model=ApiKeyOrgFanoutResponse)
def api_get_api_key_org_fanout(
    limit: int = Query(default=50, ge=1, le=1000),
    include_empty: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> ApiKeyOrgFanoutResponse:
    return get_api_key_org_fanout(
        session=session,
        limit=limit,
        include_empty=include_empty,
    )


if __name__ == "__main__":
    from datetime import timedelta

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
    Org.__table__.create(engine)
    ApiKey.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)

    secret_hash = "contract-secret-key-hash"
    with SessionLocal() as session:
        session.add_all(
            [
                Org(id="org-1", name="First Organisation"),
                Org(id="org-2", name="Second Organisation"),
            ]
        )
        session.flush()
        now = datetime.now(timezone.utc)
        session.add_all(
            [
                ApiKey(
                    id="key-1",
                    org_id="org-1",
                    key_hash=secret_hash,
                    label="first",
                    created_at=now - timedelta(days=10),
                ),
                ApiKey(
                    id="key-2",
                    org_id="org-1",
                    key_hash="another-secret-key-hash",
                    label="second",
                    created_at=now - timedelta(days=2),
                ),
                ApiKey(
                    id="key-3",
                    org_id="org-2",
                    key_hash="third-secret-key-hash",
                    label="third",
                    created_at=now - timedelta(days=5),
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/api-key-org-fanout")
        assert response.status_code == 200
        body = response.json()
        assert body["total_keys"] == 3
        assert any(org["key_count"] == 2 for org in body["orgs"])
        assert secret_hash not in response.text

    print("PASS")