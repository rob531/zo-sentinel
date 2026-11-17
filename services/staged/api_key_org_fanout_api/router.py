from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org

try:
    from .logic import get_api_key_org_fanout
except ImportError:
    get_api_key_org_fanout = None


router = APIRouter(prefix="/api")


class OrgFanoutItem(BaseModel):
    org_id: str
    org_name: str
    key_count: int
    oldest_key_age_days: float
    newest_key_age_days: float


class OrgFanoutResponse(BaseModel):
    orgs: list[OrgFanoutItem]
    total_keys: int


def _age_days(created_at: datetime, now: datetime) -> float:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return max(0.0, (now - created_at).total_seconds() / 86400.0)


def _load_fanout(
    session: Session,
    limit: int,
    include_empty: bool,
) -> OrgFanoutResponse:
    now = datetime.now(timezone.utc)

    key_rows = session.execute(
        select(ApiKey.id, ApiKey.org_id, ApiKey.created_at, Org.name)
        .select_from(ApiKey)
        .outerjoin(Org, Org.id == ApiKey.org_id)
    ).all()

    grouped: dict[str, dict] = {}
    total_keys = 0

    for _, org_id, created_at, org_name in key_rows:
        if org_id is None:
            continue

        org_key = str(org_id)
        total_keys += 1
        item = grouped.setdefault(
            org_key,
            {
                "org_id": org_key,
                "org_name": org_name or "",
                "ages": [],
            },
        )
        item["ages"].append(_age_days(created_at, now))

    if include_empty:
        for org_id, org_name in session.execute(select(Org.id, Org.name)).all():
            org_key = str(org_id)
            if org_key not in grouped:
                grouped[org_key] = {
                    "org_id": org_key,
                    "org_name": org_name or "",
                    "ages": [],
                }

    items: list[OrgFanoutItem] = []
    for item in grouped.values():
        ages = item.pop("ages")
        if not ages:
            if not include_empty:
                continue
            items.append(
                OrgFanoutItem(
                    **item,
                    key_count=0,
                    oldest_key_age_days=0.0,
                    newest_key_age_days=0.0,
                )
            )
            continue

        items.append(
            OrgFanoutItem(
                **item,
                key_count=len(ages),
                oldest_key_age_days=float(max(ages)),
                newest_key_age_days=float(min(ages)),
            )
        )

    items.sort(key=lambda item: (-item.key_count, item.org_id))
    items = items[:limit]

    return OrgFanoutResponse(orgs=items, total_keys=total_keys)


@router.get(
    "/api-key-org-fanout",
    response_model=OrgFanoutResponse,
    summary="Report API-key fanout per organisation",
)
def get_api_key_org_fanout_endpoint(
    limit: int = Query(50, ge=1, le=500),
    include_empty: bool = Query(False),
    session: Session = Depends(get_session),
) -> OrgFanoutResponse:
    return _load_fanout(session=session, limit=limit, include_empty=include_empty)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(test_engine, checkfirst=True)
    ApiKey.__table__.create(test_engine, checkfirst=True)

    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    test_session = TestSession()

    test_session.add_all(
        [
            Org(id="org-1", name="Organisation One"),
            Org(id="org-2", name="Organisation Two"),
        ]
    )
    test_session.flush()

    now = datetime.now(timezone.utc)
    test_session.add_all(
        [
            ApiKey(
                id="key-1",
                org_id="org-1",
                key_hash="literal-key-hash-value-1",
                created_at=now,
            ),
            ApiKey(
                id="key-2",
                org_id="org-1",
                key_hash="literal-key-hash-value-2",
                created_at=now,
            ),
            ApiKey(
                id="key-3",
                org_id="org-2",
                key_hash="literal-key-hash-value-3",
                created_at=now,
            ),
        ]
    )
    test_session.commit()

    test_app = router.__class__ and __import__("fastapi").FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: test_session

    response = TestClient(test_app).get("/api/api-key-org-fanout")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_keys"] == 3
    assert any(org["key_count"] == 2 for org in payload["orgs"])
    assert "literal-key-hash-value-1" not in response.text
    assert "literal-key-hash-value-2" not in response.text
    assert "literal-key-hash-value-3" not in response.text

    test_session.close()
    test_engine.dispose()
    print("PASS")