"""logic.py -- vulnerability link age distribution service logic."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy import case, create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, VulnLink


BUCKETS = ("0_7d", "8_30d", "31_90d", "91_365d", "gt_365d")


def _clamp_days(days: Any) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        value = 90
    return max(1, min(3650, value))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_vuln_link_age_distribution(db: Session, days: int = 90) -> Dict[str, Any]:
    """Return vulnerability-link age buckets and the most-linked servers."""
    normalized_days = _clamp_days(days)
    now = _utc_now()

    cutoff_7 = now - timedelta(days=7)
    cutoff_30 = now - timedelta(days=30)
    cutoff_90 = now - timedelta(days=90)
    cutoff_365 = now - timedelta(days=365)

    bucket_expression = case(
        (VulnLink.linked_at >= cutoff_7, "0_7d"),
        (VulnLink.linked_at >= cutoff_30, "8_30d"),
        (VulnLink.linked_at >= cutoff_90, "31_90d"),
        (VulnLink.linked_at >= cutoff_365, "91_365d"),
        else_="gt_365d",
    )

    # date_trunc is the PostgreSQL implementation; SQLite uses its equivalent
    # date() function so the same ORM query remains usable in the self-test.
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        day_expression = func.date(VulnLink.linked_at)
    else:
        day_expression = func.date_trunc("day", VulnLink.linked_at)

    bucket_rows = db.execute(
        select(
            day_expression.label("linked_day"),
            bucket_expression.label("bucket"),
            func.count().label("link_count"),
        )
        .group_by(day_expression, bucket_expression)
    ).all()

    counts = {bucket: 0 for bucket in BUCKETS}
    for _linked_day, bucket, link_count in bucket_rows:
        if bucket in counts:
            counts[bucket] += int(link_count)

    total_links = int(
        db.execute(select(func.count(VulnLink.id))).scalar_one()
    )
    servers_with_links = int(
        db.execute(
            select(func.count(func.distinct(VulnLink.server_id)))
        ).scalar_one()
    )

    top_rows = db.execute(
        select(
            VulnLink.server_id,
            McpServerRegistry.name,
            func.count(VulnLink.id).label("link_count"),
        )
        .join(McpServerRegistry, VulnLink.server_id == McpServerRegistry.server_id)
        .group_by(VulnLink.server_id, McpServerRegistry.name)
        .order_by(func.count(VulnLink.id).desc(), VulnLink.server_id.asc())
        .limit(20)
    ).all()

    top_servers: List[Dict[str, Any]] = [
        {
            "server_id": str(server_id),
            "name": name or "",
            "link_count": int(link_count),
        }
        for server_id, name, link_count in top_rows
    ]

    buckets = [
        {
            "bucket": bucket,
            "count": counts[bucket],
            "pct": float(counts[bucket] / total_links * 100) if total_links else 0.0,
        }
        for bucket in BUCKETS
    ]

    return {
        "days": normalized_days,
        "buckets": buckets,
        "total_links": total_links,
        "servers_with_links": servers_with_links,
        "top_servers": top_servers,
    }


def get_link_age_distribution(db: Session, days: int = 90) -> Dict[str, Any]:
    """Compatibility name for callers using the shorter service name."""
    return get_vuln_link_age_distribution(db, days)


def compute_vuln_link_age_distribution(
    db: Session, days: int = 90
) -> Dict[str, Any]:
    """Compatibility name for computation-oriented callers."""
    return get_vuln_link_age_distribution(db, days)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(
        engine,
        tables=[McpServerRegistry.__table__, VulnLink.__table__],
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()

    server_id = "srv-age-test"
    session.add(McpServerRegistry(server_id=server_id, name="Age Test Server"))
    base = _utc_now()
    session.add_all(
        [
            VulnLink(
                id=1,
                advisory_id="adv-1",
                server_id=server_id,
                match_basis="cve",
                match_value="CVE-0001",
                match_confidence=0.99,
                linked_at=base - timedelta(days=2),
            ),
            VulnLink(
                id=2,
                advisory_id="adv-2",
                server_id=server_id,
                match_basis="cve",
                match_value="CVE-0002",
                match_confidence=0.98,
                linked_at=base - timedelta(days=15),
            ),
            VulnLink(
                id=3,
                advisory_id="adv-3",
                server_id=server_id,
                match_basis="cve",
                match_value="CVE-0003",
                match_confidence=0.97,
                linked_at=base - timedelta(days=60),
            ),
            VulnLink(
                id=4,
                advisory_id="adv-4",
                server_id=server_id,
                match_basis="cve",
                match_value="CVE-0004",
                match_confidence=0.96,
                linked_at=base - timedelta(days=400),
            ),
        ]
    )
    session.commit()

    def override_get_session():
        yield session

    local_app = FastAPI()

    @local_app.get("/api/vuln/link-age")
    def link_age(days: int = 90, db: Session = Depends(get_session)):
        return get_vuln_link_age_distribution(db, days)

    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/vuln/link-age", params={"days": 90})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_links"] == 4
        assert {item["bucket"] for item in payload["buckets"]} == set(BUCKETS)
        assert sum(item["count"] for item in payload["buckets"]) == 4
        assert any(
            item["server_id"] == server_id for item in payload["top_servers"]
        )

    print("PASS")