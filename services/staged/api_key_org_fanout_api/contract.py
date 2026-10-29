from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ApiKey, Org


class OrgFanout(BaseModel):
    org_id: str
    org_name: str
    key_count: int
    oldest_key_age_days: float
    newest_key_age_days: float


class OrgFanoutResponse(BaseModel):
    orgs: List[OrgFanout]
    total_keys: int


router = APIRouter(prefix="/api")


@router.get("/api-key-org-fanout", response_model=OrgFanoutResponse)
def get_api_key_org_fanout(
    limit: int = Query(default=50),
    include_empty: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> OrgFanoutResponse:
    keyed = (
        select(
            ApiKey.org_id.label("org_id"),
            func.count(ApiKey.id).label("key_count"),
            func.min(ApiKey.created_at).label("oldest_created_at"),
            func.max(ApiKey.created_at).label("newest_created_at"),
        )
        .group_by(ApiKey.org_id)
        .subquery()
    )

    if include_empty:
        statement = (
            select(
                Org.id.label("org_id"),
                Org.name.label("org_name"),
                keyed.c.key_count,
                keyed.c.oldest_created_at,
                keyed.c.newest_created_at,
            )
            .select_from(Org)
            .outerjoin(keyed, Org.id == keyed.c.org_id)
        )
    else:
        statement = (
            select(
                Org.id.label("org_id"),
                Org.name.label("org_name"),
                keyed.c.key_count,
                keyed.c.oldest_created_at,
                keyed.c.newest_created_at,
            )
            .select_from(keyed)
            .join(Org, Org.id == keyed.c.org_id)
        )

    statement = statement.order_by(
        keyed.c.key_count.desc(),
        Org.name.asc(),
        Org.id.asc(),
    ).limit(limit)

    rows = session.execute(statement).all()
    now = datetime.now(timezone.utc)

    def age_days(value: Optional[datetime]) -> float:
        if value is None:
            return 0.0
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return max(0.0, (now - value).total_seconds() / 86400.0)

    orgs = [
        OrgFanout(
            org_id=str(row.org_id),
            org_name=str(row.org_name),
            key_count=int(row.key_count or 0),
            oldest_key_age_days=age_days(row.oldest_created_at),
            newest_key_age_days=age_days(row.newest_created_at),
        )
        for row in rows
    ]

    return OrgFanoutResponse(
        orgs=orgs,
        total_keys=sum(org.key_count for org in orgs),
    )


app = FastAPI(title="api_key_org_fanout_api")
app.include_router(router)


def main() -> int:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Org.__table__.create(engine)
    ApiKey.__table__.create(engine)

    SessionLocal = sessionmaker = __import__(
        "sqlalchemy.orm", fromlist=["sessionmaker"]
    ).sessionmaker
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    first_hash = "literal-secret-key-hash-value-1"
    second_hash = "literal-secret-key-hash-value-2"
    third_hash = "literal-secret-key-hash-value-3"

    with session_factory() as session:
        session.add_all(
            [
                Org(id="org-1", name="First Organisation", created_at=now),
                Org(id="org-2", name="Second Organisation", created_at=now),
                ApiKey(
                    id="key-1",
                    org_id="org-1",
                    key_hash=first_hash,
                    label="first",
                    created_at=now,
                ),
                ApiKey(
                    id="key-2",
                    org_id="org-1",
                    key_hash=second_hash,
                    label="second",
                    created_at=now,
                ),
                ApiKey(
                    id="key-3",
                    org_id="org-2",
                    key_hash=third_hash,
                    label="third",
                    created_at=now,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app = FastAPI(title="api_key_org_fanout_api_test")
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/api-key-org-fanout")

        assert response.status_code == 200
        payload = response.json()
        assert any(org["key_count"] == 2 for org in payload["orgs"])
        assert payload["total_keys"] == 3
        assert first_hash not in response.text
        assert second_hash not in response.text
        assert third_hash not in response.text
    finally:
        engine.dispose()

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())