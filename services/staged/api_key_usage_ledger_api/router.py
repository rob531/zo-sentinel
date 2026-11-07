from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_usage_ledger
except ImportError:
    try:
        from .logic import get_api_key_usage_ledger as get_usage_ledger
    except ImportError:
        from .logic import build_usage_ledger as get_usage_ledger


router = APIRouter(prefix="/api", tags=["api-key-usage-ledger"])


class UsageKey(BaseModel):
    key_id: str
    org_id: str
    label: str
    created_at: datetime
    age_days: int
    owner_email: str | None = None
    owner_role: str | None = None
    org_name: str | None = None


class UsageOrg(BaseModel):
    org_id: str
    org_name: str | None = None
    key_count: int
    keys: list[UsageKey]


class UsageLedgerResponse(BaseModel):
    total: int
    orgs: list[UsageOrg]
    generated_at: datetime


def _ledger_response(payload: Any) -> UsageLedgerResponse:
    if isinstance(payload, UsageLedgerResponse):
        return payload

    if isinstance(payload, dict) and "orgs" in payload:
        return UsageLedgerResponse(
            total=int(payload.get("total", 0)),
            orgs=payload.get("orgs") or [],
            generated_at=payload.get("generated_at") or datetime.now(timezone.utc),
        )

    if isinstance(payload, dict) and "keys" in payload:
        keys = payload.get("keys") or []
        generated_at = payload.get("generated_at") or datetime.now(timezone.utc)
    elif isinstance(payload, list):
        keys = payload
        generated_at = datetime.now(timezone.utc)
    else:
        keys = []
        generated_at = datetime.now(timezone.utc)

    grouped: dict[str, dict[str, Any]] = {}
    for item in keys:
        key = item if isinstance(item, dict) else item.__dict__
        org_id = str(key.get("org_id", ""))
        org = grouped.setdefault(
            org_id,
            {
                "org_id": org_id,
                "org_name": key.get("org_name"),
                "keys": [],
            },
        )
        if org.get("org_name") is None:
            org["org_name"] = key.get("org_name")
        org["keys"].append(
            {
                "key_id": str(key.get("key_id", key.get("id", ""))),
                "org_id": str(key.get("org_id", "")),
                "label": key.get("label") or "",
                "created_at": key.get("created_at"),
                "age_days": key.get("age_days", 0),
                "owner_email": key.get("owner_email"),
                "owner_role": key.get("owner_role"),
                "org_name": key.get("org_name"),
            }
        )

    orgs = []
    for org in grouped.values():
        orgs.append(
            {
                "org_id": org["org_id"],
                "org_name": org["org_name"],
                "key_count": len(org["keys"]),
                "keys": org["keys"],
            }
        )

    return UsageLedgerResponse(
        total=len(keys),
        orgs=orgs,
        generated_at=generated_at,
    )


@router.get("/keys/usage", response_model=UsageLedgerResponse)
def get_api_key_usage_ledger(
    db: Session = Depends(get_session),
) -> UsageLedgerResponse:
    return _ledger_response(get_usage_ledger(db))


if __name__ == "__main__":
    from uuid import uuid4

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import ApiKey, Org, User

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    ApiKey.__table__.create(bind=engine)
    Org.__table__.create(bind=engine)
    User.__table__.create(bind=engine)

    session = TestingSession()
    org_a = Org(id="org-a", name="Organization A")
    org_b = Org(id="org-b", name="Organization B")
    session.add_all([org_a, org_b])
    session.flush()

    session.add_all(
        [
            User(
                id="user-a",
                org_id=org_a.id,
                email="a@example.com",
                password_hash="test",
                role="owner",
            ),
            User(
                id="user-b",
                org_id=org_b.id,
                email="b@example.com",
                password_hash="test",
                role="admin",
            ),
            User(
                id="user-c",
                org_id=org_a.id,
                email="c@example.com",
                password_hash="test",
                role="member",
            ),
        ]
    )
    session.add_all(
        [
            ApiKey(id="key-a", org_id=org_a.id, key_hash="hash-a", label="A"),
            ApiKey(id="key-b", org_id=org_b.id, key_hash="hash-b", label="B"),
            ApiKey(id="key-c", org_id=org_a.id, key_hash="hash-c", label="C"),
        ]
    )
    session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: session

    response = TestClient(test_app).get("/api/keys/usage")
    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 3
    assert len(body["orgs"]) == 2

    session.close()
    engine.dispose()
    print("PASS")