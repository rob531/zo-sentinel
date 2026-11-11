from datetime import datetime, timezone

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import String, case, cast, distinct, func, select

from app.db import get_session
from app.models import User


class ClerkSyncSourceCount(BaseModel):
    source: str
    users: int
    orgs: int


class ClerkSyncStatusResponse(BaseModel):
    by_source: list[ClerkSyncSourceCount]
    never_synced: int
    total_users: int
    generated_at: str


def get_clerk_sync_status(
    org_id: str | None = None,
    db=Depends(get_session),
) -> ClerkSyncStatusResponse:
    source = func.coalesce(User.clerk_synced_via, "local").label("source")
    users = func.count(User.id).label("users")
    orgs = func.count(distinct(User.org_id)).label("orgs")
    never_synced = func.sum(
        case((User.clerk_id.is_(None), 1), else_=0)
    ).label("never_synced")

    statement = select(source, users, orgs, never_synced)

    if org_id is not None:
        statement = statement.where(
            cast(User.org_id, String) == str(org_id)
        )

    statement = statement.group_by(source).order_by(source)

    by_source: list[ClerkSyncSourceCount] = []
    never_synced_total = 0

    for row in db.execute(statement).all():
        by_source.append(
            ClerkSyncSourceCount(
                source=str(row[0]),
                users=int(row[1] or 0),
                orgs=int(row[2] or 0),
            )
        )
        never_synced_total += int(row[3] or 0)

    return ClerkSyncStatusResponse(
        by_source=by_source,
        never_synced=never_synced_total,
        total_users=sum(group.users for group in by_source),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


get_user_clerk_sync_status = get_clerk_sync_status
get_sync_status = get_clerk_sync_status


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    seeded_users = [
        {
            "email": "one@example.com",
            "org_id": "org-a",
            "clerk_synced_via": "webhook",
            "clerk_id": "clerk_1",
        },
        {
            "email": "two@example.com",
            "org_id": "org-a",
            "clerk_synced_via": "webhook",
            "clerk_id": "clerk_2",
        },
        {
            "email": "three@example.com",
            "org_id": "org-b",
            "clerk_synced_via": "oauth",
            "clerk_id": "clerk_3",
        },
        {
            "email": "four@example.com",
            "org_id": "org-b",
            "clerk_synced_via": "oauth",
            "clerk_id": "clerk_4",
        },
        {
            "email": "five@example.com",
            "org_id": "org-c",
            "clerk_synced_via": None,
            "clerk_id": None,
        },
    ]

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _Session:
        def execute(self, statement):
            grouped = {}
            for user in seeded_users:
                source = user["clerk_synced_via"] or "local"
                group = grouped.setdefault(
                    source,
                    {"users": 0, "orgs": set(), "never_synced": 0},
                )
                group["users"] += 1
                if user["org_id"] is not None:
                    group["orgs"].add(user["org_id"])
                if user["clerk_id"] is None:
                    group["never_synced"] += 1

            rows = [
                (
                    source,
                    values["users"],
                    len(values["orgs"]),
                    values["never_synced"],
                )
                for source, values in sorted(grouped.items())
            ]
            return _Result(rows)

    session = _Session()

    def override_get_session():
        yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get(
        "/api/users/clerk-sync-status",
        response_model=ClerkSyncStatusResponse,
    )
    def clerk_sync_status(
        org_id: str | None = None,
        db=Depends(get_session),
    ):
        return get_clerk_sync_status(org_id=org_id, db=db)

    with TestClient(that_app) as client:
        response = client.get("/api/users/clerk-sync-status")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_users"] == 5
        assert payload["never_synced"] == 1
        assert sum(group["users"] for group in payload["by_source"]) == 5

    print("PASS")