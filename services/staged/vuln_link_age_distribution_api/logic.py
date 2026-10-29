from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnLink

router = APIRouter(prefix="/api", tags=["vulnerability links"])

BUCKET_LABELS = (
    "0-7d",
    "8-30d",
    "31-90d",
    "91-180d",
    "181-365d",
    ">365d",
)


class AgeBucket(BaseModel):
    label: str
    count: int = Field(ge=0)


class ServerLinkAge(BaseModel):
    server_id: str
    name: Optional[str] = None
    link_count: int = Field(ge=1)
    oldest_linked_at: Optional[str] = None


class AgeDistributionResponse(BaseModel):
    days: int
    generated_at: str
    buckets: List[AgeBucket]
    total_links: int = Field(ge=0)
    stale_links: int = Field(ge=0)
    by_server: List[ServerLinkAge]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _isoformat(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _bucket_for_age(age_days: float) -> str:
    if age_days <= 7:
        return "0-7d"
    if age_days <= 30:
        return "8-30d"
    if age_days <= 90:
        return "31-90d"
    if age_days <= 180:
        return "91-180d"
    if age_days <= 365:
        return "181-365d"
    return ">365d"


def compute_age_distribution(
    session: Session,
    days: int,
    server_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> AgeDistributionResponse:
    generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = generated_at - timedelta(days=days)

    query = session.query(
        VulnLink.id,
        VulnLink.linked_at,
        VulnLink.server_id,
        McpServerRegistry.name,
    ).outerjoin(
        McpServerRegistry,
        McpServerRegistry.server_id == VulnLink.server_id,
    )

    if server_id is not None:
        query = query.filter(VulnLink.server_id == server_id)

    rows = query.all()
    counts: Dict[str, int] = {label: 0 for label in BUCKET_LABELS}
    servers: Dict[str, Dict[str, object]] = {}
    stale_links = 0

    for _, linked_at, linked_server_id, server_name in rows:
        if linked_at is None:
            counts[">365d"] += 1
            stale_links += 1
            linked_time = None
        else:
            linked_time = _as_utc(linked_at)
            age_days = max(0.0, (generated_at - linked_time).total_seconds() / 86400.0)
            counts[_bucket_for_age(age_days)] += 1
            if linked_time < cutoff:
                stale_links += 1

        key = str(linked_server_id)
        server = servers.setdefault(
            key,
            {
                "server_id": key,
                "name": server_name,
                "link_count": 0,
                "oldest_linked_at": None,
            },
        )
        server["link_count"] = int(server["link_count"]) + 1
        if linked_time is not None:
            oldest = server["oldest_linked_at"]
            if oldest is None or linked_time < oldest:
                server["oldest_linked_at"] = linked_time

    by_server = [
        ServerLinkAge(
            server_id=str(server["server_id"]),
            name=server["name"],
            link_count=int(server["link_count"]),
            oldest_linked_at=_isoformat(server["oldest_linked_at"]),
        )
        for server in sorted(servers.values(), key=lambda item: str(item["server_id"]))
    ]

    return AgeDistributionResponse(
        days=days,
        generated_at=generated_at.isoformat().replace("+00:00", "Z"),
        buckets=[AgeBucket(label=label, count=counts[label]) for label in BUCKET_LABELS],
        total_links=len(rows),
        stale_links=stale_links,
        by_server=by_server,
    )


@router.get(
    "/vuln/links/age-distribution",
    response_model=AgeDistributionResponse,
)
def get_vuln_link_age_distribution(
    days: int = Query(default=30, ge=1, le=365),
    server_id: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> AgeDistributionResponse:
    return compute_age_distribution(session=session, days=days, server_id=server_id)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE vuln_links ("
                "id INTEGER PRIMARY KEY, "
                "linked_at DATETIME, "
                "server_id TEXT"
                ")"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE mcp_server_registry ("
                "server_id TEXT PRIMARY KEY, "
                "name TEXT"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO mcp_server_registry (server_id, name) "
                "VALUES (:server_id, :name)"
            ),
            [
                {"server_id": "server-a", "name": "Server A"},
                {"server_id": "server-b", "name": "Server B"},
            ],
        )

    now = datetime.now(timezone.utc)
    link_times = (
        now,
        now - timedelta(hours=1),
        now - timedelta(days=45),
        now - timedelta(days=100),
    )
    server_ids = ("server-a", "server-a", "server-b", "server-b")

    with TestSession() as session:
        for link_id, (linked_at, linked_server_id) in enumerate(
            zip(link_times, server_ids),
            start=1,
        ):
            session.execute(
                text(
                    "INSERT INTO vuln_links (id, linked_at, server_id) "
                    "VALUES (:id, :linked_at, :server_id)"
                ),
                {
                    "id": link_id,
                    "linked_at": linked_at.replace(tzinfo=None),
                    "server_id": linked_server_id,
                },
            )
        session.commit()

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/vuln/links/age-distribution",
        params={"days": 30},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_links"] == 4
    assert sum(bucket["count"] for bucket in payload["buckets"]) == payload["total_links"]
    assert payload["stale_links"] == 2
    print("PASS")