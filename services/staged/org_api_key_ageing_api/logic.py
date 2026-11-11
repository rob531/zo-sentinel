from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


STALE_AFTER_DAYS = 90


class OrgApiKey(BaseModel):
    id: str
    org_id: str
    org_name: str
    label: str
    created_at: str
    age_days: int
    stale: bool


class OrgApiKeysResponse(BaseModel):
    total: int
    stale_after_days: int
    keys: List[OrgApiKey]


router = APIRouter(prefix="/api")


def _parse_created_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _serialize_created_at(value: Any) -> str:
    return _parse_created_at(value).isoformat()


@router.get(
    "/orgs/api-keys",
    response_model=OrgApiKeysResponse,
    summary="List organisation API keys with age and rotation status",
)
def list_org_api_keys(
    org_id: Optional[str] = Query(None),
    session: Session = Depends(get_session),
) -> OrgApiKeysResponse:
    sql = """
        SELECT
            api_keys.id,
            api_keys.org_id,
            orgs.name AS org_name,
            api_keys.label,
            api_keys.created_at
        FROM api_keys
        JOIN orgs ON orgs.id = api_keys.org_id
    """
    params: Dict[str, Any] = {}

    if org_id is not None:
        sql += " WHERE api_keys.org_id = :org_id"
        params["org_id"] = org_id

    sql += " ORDER BY api_keys.created_at ASC"

    now = datetime.now(timezone.utc)
    keys: List[OrgApiKey] = []

    for row in session.execute(text(sql), params).mappings():
        created_at = _parse_created_at(row["created_at"])
        age_days = max(0, (now - created_at).days)

        keys.append(
            OrgApiKey(
                id=str(row["id"]),
                org_id=str(row["org_id"]),
                org_name=str(row["org_name"]),
                label=str(row["label"] or ""),
                created_at=_serialize_created_at(row["created_at"]),
                age_days=age_days,
                stale=age_days > STALE_AFTER_DAYS,
            )
        )

    return OrgApiKeysResponse(
        total=len(keys),
        stale_after_days=STALE_AFTER_DAYS,
        keys=keys,
    )


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE orgs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE api_keys (
                id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                label TEXT,
                created_at TEXT,
                key_hash TEXT
            )
            """
        )

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.execute(
            text(
                "INSERT INTO orgs (id, name, created_at) VALUES "
                "(:id, :name, :created_at)"
            ),
            [
                {"id": "org-1", "name": "First Organisation", "created_at": now.isoformat()},
                {"id": "org-2", "name": "Second Organisation", "created_at": now.isoformat()},
            ],
        )
        session.execute(
            text(
                "INSERT INTO api_keys "
                "(id, org_id, label, created_at, key_hash) VALUES "
                "(:id, :org_id, :label, :created_at, :key_hash)"
            ),
            [
                {
                    "id": "key-1",
                    "org_id": "org-1",
                    "label": "Old key",
                    "created_at": (now - __import__("datetime").timedelta(days=91)).isoformat(),
                    "key_hash": "secret-old-hash",
                },
                {
                    "id": "key-2",
                    "org_id": "org-1",
                    "label": "Recent key",
                    "created_at": (now - __import__("datetime").timedelta(days=30)).isoformat(),
                    "key_hash": "secret-recent-hash",
                },
                {
                    "id": "key-3",
                    "org_id": "org-2",
                    "label": "Current key",
                    "created_at": now.isoformat(),
                    "key_hash": "secret-current-hash",
                },
            ],
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/orgs/api-keys")
    payload = response.json()

    assert response.status_code == 200
    assert payload["total"] == 3
    assert payload["stale_after_days"] == 90
    assert len(payload["keys"]) == 3
    assert all("key_hash" not in item for item in payload["keys"])
    assert sum(1 for item in payload["keys"] if item["stale"]) == 1

    print("PASS")