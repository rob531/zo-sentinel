from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org, User


router = APIRouter(prefix="/api")


class OrgHygiene(BaseModel):
    org_id: str
    org_name: str
    users: int
    keys: int
    unlabelled_keys: int
    keys_older_than_days: int
    inactive_users: int
    findings: List[str]


class HygieneTotals(BaseModel):
    orgs: int
    users: int
    keys: int
    findings: int


class HygieneResponse(BaseModel):
    days: int
    generated_at: str
    orgs: List[OrgHygiene]
    totals: HygieneTotals


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_unlabelled(label: object) -> bool:
    return label is None or not str(label).strip()


def get_org_key_hygiene(db: Session, days: int = 90) -> HygieneResponse:
    """Calculate per-organisation API-key and user-account hygiene."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    org_rows = db.execute(select(Org)).scalars().all()
    user_rows = db.execute(select(User)).scalars().all()
    key_rows = db.execute(select(ApiKey)).scalars().all()

    users_by_org: Dict[str, List[User]] = {}
    for user in user_rows:
        users_by_org.setdefault(str(user.org_id), []).append(user)

    keys_by_org: Dict[str, List[ApiKey]] = {}
    for key in key_rows:
        keys_by_org.setdefault(str(key.org_id), []).append(key)

    results: List[OrgHygiene] = []

    for org in org_rows:
        org_id = str(org.id)
        users = users_by_org.get(org_id, [])
        keys = keys_by_org.get(org_id, [])

        unlabelled_keys = sum(1 for key in keys if _is_unlabelled(key.label))
        stale_keys = sum(
            1 for key in keys if _utc(key.created_at) < cutoff
        )

        keys_created_after_cutoff = any(
            _utc(key.created_at) > cutoff for key in keys
        )
        inactive_users = sum(
            1
            for user in users
            if _utc(user.created_at) < cutoff
            and not keys_created_after_cutoff
        )

        findings: List[str] = []
        if unlabelled_keys:
            findings.append("unlabelled_key")
        if stale_keys:
            findings.append("stale_key")
        if inactive_users:
            findings.append("inactive_user")
        if not keys:
            findings.append("no_keys")
        if not users:
            findings.append("no_users")

        results.append(
            OrgHygiene(
                org_id=org_id,
                org_name=org.name,
                users=len(users),
                keys=len(keys),
                unlabelled_keys=unlabelled_keys,
                keys_older_than_days=stale_keys,
                inactive_users=inactive_users,
                findings=findings,
            )
        )

    results.sort(key=lambda item: (-len(item.findings), item.org_id))

    return HygieneResponse(
        days=days,
        generated_at=datetime.now(timezone.utc).isoformat(),
        orgs=results,
        totals=HygieneTotals(
            orgs=len(results),
            users=sum(item.users for item in results),
            keys=sum(item.keys for item in results),
            findings=sum(len(item.findings) for item in results),
        ),
    )


@router.get("/orgs/hygiene", response_model=HygieneResponse)
def org_key_hygiene(
    days: int = Query(default=90, ge=0),
    db: Session = Depends(get_session),
) -> HygieneResponse:
    return get_org_key_hygiene(db, days=days)


app = FastAPI()
app.include_router(router)


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
    Org.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with SessionLocal() as session:
        now = datetime.now(timezone.utc)
        session.add_all(
            [
                Org(id="org-a", name="Organisation A"),
                Org(id="org-b", name="Organisation B"),
                User(
                    id="user-a",
                    email="a@example.test",
                    org_id="org-a",
                    role="member",
                    password_hash="hash-a",
                    created_at=now - timedelta(days=300),
                ),
                User(
                    id="user-b",
                    email="b@example.test",
                    org_id="org-a",
                    role="member",
                    password_hash="hash-b",
                    created_at=now - timedelta(days=30),
                ),
                User(
                    id="user-c",
                    email="c@example.test",
                    org_id="org-b",
                    role="member",
                    password_hash="hash-c",
                    created_at=now - timedelta(days=30),
                ),
                ApiKey(
                    id="key-a",
                    org_id="org-a",
                    key_hash="key-hash-a",
                    label="",
                    created_at=now - timedelta(days=200),
                ),
                ApiKey(
                    id="key-b",
                    org_id="org-a",
                    key_hash="key-hash-b",
                    label="current",
                    created_at=now - timedelta(days=10),
                ),
                ApiKey(
                    id="key-c",
                    org_id="org-a",
                    key_hash="key-hash-c",
                    label="current",
                    created_at=now - timedelta(days=5),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/orgs/hygiene", params={"days": 90})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["orgs"]) == 2
    assert any(
        "unlabelled_key" in org["findings"] for org in payload["orgs"]
    )
    assert any("stale_key" in org["findings"] for org in payload["orgs"])
    assert "@" not in response.text
    print("PASS")