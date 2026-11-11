from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, VulnLink


class AgeBucket(BaseModel):
    label: str
    count: int


class ServerLinkAge(BaseModel):
    server_id: str
    name: Optional[str]
    link_count: int
    oldest_linked_at: datetime


class VulnLinkAgeDistributionResponse(BaseModel):
    days: int
    generated_at: datetime
    buckets: List[AgeBucket]
    total_links: int
    stale_links: int
    by_server: List[ServerLinkAge]


router = APIRouter(prefix="/api")
app = FastAPI()
app.include_router(router)

_BUCKET_LABELS = ("0-7d", "8-30d", "31-90d", "91-365d", "365+d")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 7:
        return "0-7d"
    if age_days <= 30:
        return "8-30d"
    if age_days <= 90:
        return "31-90d"
    if age_days <= 365:
        return "91-365d"
    return "365+d"


@router.get(
    "/vuln/links/age-distribution",
    response_model=VulnLinkAgeDistributionResponse,
)
def get_vuln_link_age_distribution(
    days: int = Query(default=30, ge=1, le=365),
    server_id: Optional[str] = Query(default=None),
    db=Depends(get_session),
) -> VulnLinkAgeDistributionResponse:
    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(days=days)

    statement = select(
        VulnLink.linked_at,
        VulnLink.server_id,
        McpServerRegistry.name,
    ).select_from(VulnLink).outerjoin(
        McpServerRegistry,
        McpServerRegistry.server_id == VulnLink.server_id,
    )

    if server_id is not None:
        statement = statement.where(VulnLink.server_id == server_id)

    rows = db.execute(statement).all()

    counts = {label: 0 for label in _BUCKET_LABELS}
    servers = {}
    total_links = 0
    stale_links = 0

    for linked_at, linked_server_id, server_name in rows:
        linked_at_utc = _as_utc(linked_at)
        age_days = max(0, (generated_at - linked_at_utc).days)
        counts[_bucket_for_age(age_days)] += 1
        total_links += 1

        if linked_at_utc < cutoff:
            stale_links += 1

        server = servers.setdefault(
            linked_server_id,
            {
                "server_id": linked_server_id,
                "name": server_name,
                "link_count": 0,
                "oldest_linked_at": linked_at_utc,
            },
        )
        server["link_count"] += 1
        if linked_at_utc < server["oldest_linked_at"]:
            server["oldest_linked_at"] = linked_at_utc

    by_server = [
        ServerLinkAge(
            server_id=server["server_id"],
            name=server["name"],
            link_count=server["link_count"],
            oldest_linked_at=server["oldest_linked_at"],
        )
        for server in sorted(servers.values(), key=lambda item: item["server_id"])
    ]

    return VulnLinkAgeDistributionResponse(
        days=days,
        generated_at=generated_at,
        buckets=[
            AgeBucket(label=label, count=counts[label]) for label in _BUCKET_LABELS
        ],
        total_links=total_links,
        stale_links=stale_links,
        by_server=by_server,
    )


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    McpServerRegistry.__table__.create(engine)
    VulnLink.__table__.create(engine)

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=45)
    older = now - timedelta(days=60)

    with TestingSessionLocal() as db:
        db.add(McpServerRegistry(server_id="server-a", name="Server A"))
        db.add(McpServerRegistry(server_id="server-b", name="Server B"))
        db.add_all(
            [
                VulnLink(
                    advisory_id="ADV-1",
                    server_id="server-a",
                    match_basis="advisory_id",
                    match_value="CVE-2024-0001",
                    match_confidence=1.0,
                    linked_at=now,
                ),
                VulnLink(
                    advisory_id="ADV-2",
                    server_id="server-a",
                    match_basis="advisory_id",
                    match_value="CVE-2024-0002",
                    match_confidence=1.0,
                    linked_at=now,
                ),
                VulnLink(
                    advisory_id="ADV-3",
                    server_id="server-b",
                    match_basis="advisory_id",
                    match_value="CVE-2024-0003",
                    match_confidence=1.0,
                    linked_at=old,
                ),
                VulnLink(
                    advisory_id="ADV-4",
                    server_id="server-b",
                    match_basis="advisory_id",
                    match_value="CVE-2024-0004",
                    match_confidence=1.0,
                    linked_at=older,
                ),
            ]
        )
        db.commit()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    client = TestClient(that_app)
    response = client.get("/api/vuln/links/age-distribution", params={"days": 30})
    assert response.status_code == 200
    payload = response.json()

    assert payload["days"] == 30
    assert payload["total_links"] == 4
    assert sum(bucket["count"] for bucket in payload["buckets"]) == payload["total_links"]
    assert payload["stale_links"] == 2
    assert payload["by_server"]
    assert all(server["link_count"] >= 1 for server in payload["by_server"])

    filtered = client.get(
        "/api/vuln/links/age-distribution",
        params={"days": 30, "server_id": "server-a"},
    )
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert filtered_payload["total_links"] == 2
    assert filtered_payload["stale_links"] == 0

    print("PASS")


if __name__ == "__main__":
    _run_self_test()