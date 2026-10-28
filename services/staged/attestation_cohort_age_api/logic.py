from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


COHORT_BUCKETS = ("0-7d", "8-30d", "31-90d", "91-180d", "180d+")


def _as_utc_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).date()
    return None


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 7:
        return "0-7d"
    if age_days <= 30:
        return "8-30d"
    if age_days <= 90:
        return "31-90d"
    if age_days <= 180:
        return "91-180d"
    return "180d+"


def get_attestation_age_cohort(
    days: int = 365,
    db: Session = Depends(get_session),
    session: Optional[Session] = None,
) -> dict[str, Any]:
    if isinstance(days, Session):
        days, db = 365, days
    if session is not None:
        db = session

    cutoff_days = max(0, int(days))
    today = datetime.now(timezone.utc).date()
    rows = db.execute(
        text(
            """
            SELECT id, server_id, created_at
            FROM mcp_attestations
            WHERE created_at IS NOT NULL
            """
        )
    ).mappings().all()

    cohort_counts: Counter[str] = Counter()
    server_counts: Counter[Any] = Counter()
    total_attestations = 0

    for row in rows:
        created_date = _as_utc_date(row["created_at"])
        if created_date is None:
            continue

        age_days = max(0, (today - created_date).days)
        if age_days > cutoff_days:
            continue

        total_attestations += 1
        cohort_counts[_bucket_for_age(age_days)] += 1

        server_id = row["server_id"]
        if server_id is not None:
            server_counts[server_id] += 1

    distinct_servers = len(server_counts)
    duplicate_servers = sum(1 for count in server_counts.values() if count > 1)

    return {
        "days": cutoff_days,
        "cohorts": [
            {"bucket": bucket, "count": cohort_counts.get(bucket, 0)}
            for bucket in COHORT_BUCKETS
        ],
        "distinct_servers": distinct_servers,
        "duplicate_servers": duplicate_servers,
        "total_attestations": total_attestations,
    }


get_age_cohort = get_attestation_age_cohort
get_attestation_age_cohorts = get_attestation_age_cohort
get_attestation_cohort_age = get_attestation_age_cohort
get_age_cohort_data = get_attestation_age_cohort


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    metadata = MetaData()
    attestations = Table(
        "mcp_attestations",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("server_id", String(255), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    seeded_rows = [
        {"server_id": "server-a", "created_at": now - timedelta(days=1)},
        {"server_id": "server-b", "created_at": now - timedelta(days=10)},
        {"server_id": "server-c", "created_at": now - timedelta(days=45)},
        {"server_id": "server-a", "created_at": now - timedelta(days=200)},
    ]
    with testing_session() as db:
        db.execute(insert(attestations), seeded_rows)
        db.commit()

    def testing_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    application = FastAPI()
    application.dependency_overrides[get_session] = testing_get_session

    @application.get("/api/attestations/age-cohort")
    def age_cohort_endpoint(days: int = 365, db: Session = Depends(get_session)):
        return get_attestation_age_cohort(days=days, db=db)

    response = TestClient(application).get(
        "/api/attestations/age-cohort",
        params={"days": 365},
    )
    payload = response.json()
    cohorts = {item["bucket"]: item["count"] for item in payload["cohorts"]}

    assert response.status_code == 200
    assert payload["total_attestations"] == 4
    assert payload["distinct_servers"] == 3
    assert payload["duplicate_servers"] == 1
    assert cohorts["31-90d"] == 1
    assert cohorts["91-180d"] == 0
    print("PASS")