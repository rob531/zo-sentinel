from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import User


class OrgMemberGrowthTotals(BaseModel):
    total_members: int
    distinct_orgs: int
    distinct_roles: int


class OrgMemberGrowthGroup(BaseModel):
    org_id: Optional[str]
    role: Optional[str]
    member_count: int
    first_member_at: str
    last_member_at: str


class OrgMemberGrowthResponse(BaseModel):
    days: int
    generated_at: str
    totals: OrgMemberGrowthTotals
    groups: List[OrgMemberGrowthGroup]


router = APIRouter(prefix="/api")


def _clamp_days(days: int) -> int:
    return max(1, min(365, int(days)))


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    else:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def get_org_member_growth(
    db: Any = Depends(get_session),
    days: int = 90,
    org_id: Optional[str] = None,
) -> Dict[str, Any]:
    days = _clamp_days(days)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    rows: Iterable[Tuple[Optional[str], Optional[str], datetime]] = (
        db.query(User.org_id, User.role, User.created_at).all()
    )

    grouped: Dict[Tuple[Optional[str], Optional[str]], List[datetime]] = {}
    total_members = 0
    distinct_orgs = set()
    distinct_roles = set()

    for row_org_id, row_role, created_at in rows:
        if created_at is None:
            continue
        if _naive_utc(created_at) < cutoff:
            continue
        if org_id is not None and row_org_id != org_id:
            continue

        total_members += 1
        if row_org_id is not None:
            distinct_orgs.add(row_org_id)
        if row_role is not None:
            distinct_roles.add(row_role)
        grouped.setdefault((row_org_id, row_role), []).append(created_at)

    groups = []
    for (group_org_id, group_role), member_dates in sorted(
        grouped.items(), key=lambda item: (item[0][0] or "", item[0][1] or "")
    ):
        groups.append(
            {
                "org_id": group_org_id,
                "role": group_role,
                "member_count": len(member_dates),
                "first_member_at": _iso_utc(min(member_dates)),
                "last_member_at": _iso_utc(max(member_dates)),
            }
        )

    return {
        "days": days,
        "generated_at": _iso_utc(datetime.now(timezone.utc)),
        "totals": {
            "total_members": total_members,
            "distinct_orgs": len(distinct_orgs),
            "distinct_roles": len(distinct_roles),
        },
        "groups": groups,
    }


get_member_growth = get_org_member_growth
compute_org_member_growth = get_org_member_growth


@router.get(
    "/orgs/members/growth",
    response_model=OrgMemberGrowthResponse,
)
def org_member_growth_endpoint(
    days: int = Query(90),
    org_id: Optional[str] = Query(None),
    db: Any = Depends(get_session),
) -> Dict[str, Any]:
    return get_org_member_growth(db=db, days=days, org_id=org_id)


if __name__ == "__main__":
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    emails = [
        "admin-a@example.test",
        "viewer-a@example.test",
        "admin-b@example.test",
    ]
    users = [
        User(
            id="user-a-admin",
            email=emails[0],
            password_hash="secret-a",
            org_id="org_a",
            role="admin",
            created_at=now - timedelta(days=1),
            clerk_id="clerk-a",
            clerk_synced_via="test",
            clerk_created_at=now - timedelta(days=1),
        ),
        User(
            id="user-a-viewer",
            email=emails[1],
            password_hash="secret-b",
            org_id="org_a",
            role="viewer",
            created_at=now - timedelta(days=2),
            clerk_id="clerk-b",
            clerk_synced_via="test",
            clerk_created_at=now - timedelta(days=2),
        ),
        User(
            id="user-b-admin",
            email=emails[2],
            password_hash="secret-c",
            org_id="org_b",
            role="admin",
            created_at=now - timedelta(days=3),
            clerk_id="clerk-c",
            clerk_synced_via="test",
            clerk_created_at=now - timedelta(days=3),
        ),
    ]

    class InMemoryQuery:
        def __init__(self, store: Iterable[User]) -> None:
            self.store = store

        def all(self) -> List[Tuple[Optional[str], Optional[str], datetime]]:
            return [
                (user.org_id, user.role, user.created_at)
                for user in self.store
            ]

    class InMemorySession:
        def __init__(self, store: Iterable[User]) -> None:
            self.store = list(store)

        def query(self, *entities: Any) -> InMemoryQuery:
            return InMemoryQuery(self.store)

    async def request_json(
        application: FastAPI, path: str
    ) -> Tuple[int, Dict[str, Any]]:
        status_code = 500
        body = bytearray()

        async def receive() -> Dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: Dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body":
                body.extend(message.get("body", b""))

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
        await application(scope, receive, send)
        return status_code, json.loads(body.decode())

    def assert_safe(value: Any) -> None:
        if isinstance(value, dict):
            assert "password_hash" not in value
            assert "email" not in value
            assert "clerk_id" not in value
            for child in value.values():
                assert_safe(child)
        elif isinstance(value, list):
            for child in value:
                assert_safe(child)
        elif isinstance(value, str):
            assert value not in emails

    that_app = FastAPI()
    that_app.include_router(router)
    session = InMemorySession(users)
    that_app.dependency_overrides[get_session] = lambda: session

    status, payload = asyncio.run(
        request_json(that_app, "/api/orgs/members/growth")
    )
    assert status == 200
    assert payload["totals"]["total_members"] == 3
    assert payload["totals"]["distinct_orgs"] == 2
    assert payload["totals"]["distinct_roles"] == 2

    org_a_admin = next(
        group
        for group in payload["groups"]
        if group["org_id"] == "org_a" and group["role"] == "admin"
    )
    assert org_a_admin["member_count"] == 1
    assert_safe(payload)
    print("PASS")