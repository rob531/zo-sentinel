from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Org, User


class RoleMix(BaseModel):
    role: str
    count: int


class OrgUserGrowth(BaseModel):
    org_id: str
    org_name: str
    user_count: int
    role_mix: List[RoleMix]
    first_user_at: Optional[datetime]
    last_user_at: Optional[datetime]
    users_7d: int
    users_30d: int


class UserGrowthResponse(BaseModel):
    total_orgs: int
    total_users: int
    orgs: List[OrgUserGrowth]


app = FastAPI(title="Org User Growth API")


@app.get("/api/orgs/user-growth", response_model=UserGrowthResponse)
def get_org_user_growth(db: Session = Depends(get_session)) -> UserGrowthResponse:
    org_rows = db.execute(select(Org.id, Org.name)).all()
    org_names = {row.id: row.name for row in org_rows}

    grouped_rows = db.execute(
        select(
            User.org_id,
            User.role,
            func.count(User.id).label("user_count"),
            func.min(User.created_at).label("first_user_at"),
            func.max(User.created_at).label("last_user_at"),
        )
        .group_by(User.org_id, User.role)
        .order_by(User.org_id, User.role)
    ).all()

    created_rows = db.execute(
        select(User.created_at).where(User.created_at != None)
    ).scalars().all()
    created_times = [value for value in created_rows if value is not None]
    newest_user_at = max(created_times) if created_times else None

    by_org = {}
    total_users = 0

    for row in grouped_rows:
        org = by_org.setdefault(
            row.org_id,
            {
                "user_count": 0,
                "roles": [],
                "first_user_at": None,
                "last_user_at": None,
            },
        )
        org["user_count"] += int(row.user_count)
        org["roles"].append({"role": row.role, "count": int(row.user_count)})
        total_users += int(row.user_count)

        if row.first_user_at is not None:
            if org["first_user_at"] is None or row.first_user_at < org["first_user_at"]:
                org["first_user_at"] = row.first_user_at
        if row.last_user_at is not None:
            if org["last_user_at"] is None or row.last_user_at > org["last_user_at"]:
                org["last_user_at"] = row.last_user_at

    window_counts = {}
    if newest_user_at is not None:
        seven_day_start = newest_user_at - timedelta(days=7)
        thirty_day_start = newest_user_at - timedelta(days=30)

        for created_at in created_times:
            org_id = next(
                (
                    row.org_id
                    for row in grouped_rows
                    if row.first_user_at is not None
                    and row.last_user_at is not None
                    and row.first_user_at <= created_at <= row.last_user_at
                ),
                None,
            )
            if org_id is None:
                continue
            counts = window_counts.setdefault(org_id, {"users_7d": 0, "users_30d": 0})
            if created_at >= seven_day_start:
                counts["users_7d"] += 1
            if created_at >= thirty_day_start:
                counts["users_30d"] += 1

    result = []
    for org_id, name in org_names.items():
        details = by_org.get(
            org_id,
            {
                "user_count": 0,
                "roles": [],
                "first_user_at": None,
                "last_user_at": None,
            },
        )
        counts = window_counts.get(org_id, {"users_7d": 0, "users_30d": 0})
        result.append(
            OrgUserGrowth(
                org_id=org_id,
                org_name=name,
                user_count=details["user_count"],
                role_mix=[
                    RoleMix(role=item["role"], count=item["count"])
                    for item in sorted(details["roles"], key=lambda item: item["role"])
                ],
                first_user_at=details["first_user_at"],
                last_user_at=details["last_user_at"],
                users_7d=counts["users_7d"],
                users_30d=counts["users_30d"],
            )
        )

    result.sort(key=lambda item: (-item.user_count, item.org_id))

    return UserGrowthResponse(
        total_orgs=len(result),
        total_users=total_users,
        orgs=result,
    )


def run() -> None:
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(engine, checkfirst=True)
    User.__table__.create(engine, checkfirst=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    base = datetime(2025, 1, 31, 12, 0, 0)
    old = base - timedelta(days=40)

    db = SessionLocal()
    try:
        db.add_all(
            [
                Org(id="org-1", name="Acme", created_at=base - timedelta(days=100)),
                Org(id="org-2", name="Globex", created_at=base - timedelta(days=90)),
                User(
                    id="user-1",
                    email="admin1@example.com",
                    org_id="org-1",
                    role="admin",
                    created_at=base,
                    clerk_id="clerk-1",
                    clerk_created_at=base,
                    clerk_synced_via="self-test",
                    password_hash="test",
                ),
                User(
                    id="user-2",
                    email="admin2@example.com",
                    org_id="org-1",
                    role="admin",
                    created_at=base - timedelta(days=1),
                    clerk_id="clerk-2",
                    clerk_created_at=base - timedelta(days=1),
                    clerk_synced_via="self-test",
                    password_hash="test",
                ),
                User(
                    id="user-3",
                    email="viewer1@example.com",
                    org_id="org-2",
                    role="viewer",
                    created_at=base - timedelta(days=2),
                    clerk_id="clerk-3",
                    clerk_created_at=base - timedelta(days=2),
                    clerk_synced_via="self-test",
                    password_hash="test",
                ),
                User(
                    id="user-4",
                    email="viewer2@example.com",
                    org_id="org-2",
                    role="viewer",
                    created_at=old,
                    clerk_id="clerk-4",
                    clerk_created_at=old,
                    clerk_synced_via="self-test",
                    password_hash="test",
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    that_app = FastAPI()
    that_app.include_router(app.router)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app.dependency_overrides[get_session] = override_get_session

    try:
        response = TestClient(that_app).get("/api/orgs/user-growth")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_orgs"] == 2
        assert payload["total_users"] == 4

        first_org = payload["orgs"][0]
        admin_counts = {
            item["role"]: item["count"] for item in first_org["role_mix"]
        }
        assert admin_counts["admin"] == 2
        assert first_org["users_30d"] == 2
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run()