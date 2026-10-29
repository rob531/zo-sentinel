from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import DateTime, bindparam, case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org

router = APIRouter(prefix="/api", tags=["api-keys"])


def _iso8601(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def get_org_facets(session: Session = Depends(get_session)) -> Dict[str, Any]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).replace(tzinfo=None)

    statement = (
        select(
            ApiKey.org_id.label("org_id"),
            Org.name.label("org_name"),
            func.count(ApiKey.id).label("key_count"),
            func.coalesce(
                func.sum(
                    case(
                        (ApiKey.created_at >= bindparam("cutoff", type_=DateTime), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("created_last_30d"),
            func.min(ApiKey.created_at).label("oldest_key_at"),
            func.sum(
                case((Org.id.is_(None), 1), else_=0)
            ).label("keys_without_org"),
        )
        .select_from(ApiKey)
        .outerjoin(Org, ApiKey.org_id == Org.id)
        .group_by(ApiKey.org_id, Org.name)
    )

    rows = session.execute(statement, {"cutoff": cutoff}).all()

    orgs: List[Dict[str, Any]] = []
    total_keys = 0
    keys_without_org = 0

    for row in rows:
        key_count = int(row.key_count or 0)
        total_keys += key_count
        keys_without_org += int(row.keys_without_org or 0)

        if row.org_id is None:
            continue

        orgs.append(
            {
                "org_id": str(row.org_id),
                "org_name": row.org_name,
                "key_count": key_count,
                "created_last_30d": int(row.created_last_30d or 0),
                "oldest_key_at": _iso8601(row.oldest_key_at),
            }
        )

    orgs.sort(key=lambda item: item["org_id"])

    return {
        "orgs": orgs,
        "org_count": len(orgs),
        "total_keys": total_keys,
        "keys_without_org": keys_without_org,
    }


@router.get("/api-keys/org-facets")
def api_key_org_facets(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return get_org_facets(session)


if __name__ == "__main__":
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

    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with testing_session() as session:
        session.add_all(
            [
                Org(id="org-1", name="First Org", created_at=now),
                Org(id="org-2", name="Second Org", created_at=now),
                Org(id="org-3", name="Third Org", created_at=now),
            ]
        )
        session.flush()
        session.add_all(
            [
                ApiKey(
                    id="key-1",
                    org_id="org-1",
                    key_hash="hash-1",
                    label="one",
                    created_at=now - timedelta(days=2),
                ),
                ApiKey(
                    id="key-2",
                    org_id="org-1",
                    key_hash="hash-2",
                    label="two",
                    created_at=now - timedelta(days=45),
                ),
                ApiKey(
                    id="key-3",
                    org_id="org-2",
                    key_hash="hash-3",
                    label="three",
                    created_at=now - timedelta(days=10),
                ),
                ApiKey(
                    id="key-4",
                    org_id="missing-org",
                    key_hash="hash-4",
                    label="four",
                    created_at=now - timedelta(days=5),
                ),
            ]
        )
        session.commit()

    with TestClient(that_app) as client:
        response = client.get("/api/api-keys/org-facets")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_keys"] == 4
        assert payload["keys_without_org"] == 1
        assert payload["org_count"] == len(payload["orgs"])
        for org in payload["orgs"]:
            assert set(org) == {
                "org_id",
                "org_name",
                "key_count",
                "created_last_30d",
                "oldest_key_at",
            }
            assert "key_hash" not in org

    print("PASS")