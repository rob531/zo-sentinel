from datetime import datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket_for_age(age_seconds: float) -> str:
    if age_seconds <= 7 * 24 * 60 * 60:
        return "0-7d"
    if age_seconds <= 30 * 24 * 60 * 60:
        return "8-30d"
    if age_seconds <= 90 * 24 * 60 * 60:
        return "31-90d"
    return "90d+"


def _scan_backlog_rows(session: Session, reference_time: datetime) -> dict[str, Any]:
    result = session.execute(
        select(
            McpServerRegistry.last_scanned,
            McpServerRegistry.scan_count,
        )
    ).all()

    reference_time = _as_utc(reference_time) or datetime.now(timezone.utc)
    bucket_names = ["0-7d", "8-30d", "31-90d", "90d+"]
    buckets = {name: 0 for name in bucket_names}
    never_scanned = 0
    oldest: datetime | None = None
    scan_count_total = 0

    for last_scanned, scan_count in result:
        scan_count_total += int(scan_count or 0)
        scanned_at = _as_utc(last_scanned)

        if scanned_at is None:
            never_scanned += 1
            continue

        if oldest is None or scanned_at < oldest:
            oldest = scanned_at

        age_seconds = (reference_time - scanned_at).total_seconds()
        if age_seconds < 0:
            age_seconds = 0
        buckets[_bucket_for_age(age_seconds)] += 1

    total_servers = len(result)
    oldest_last_scanned = oldest.isoformat() if oldest is not None else None
    average = float(scan_count_total / total_servers) if total_servers else 0.0

    return {
        "total_servers": total_servers,
        "never_scanned": never_scanned,
        "buckets": [
            {"bucket": bucket, "count": buckets[bucket]}
            for bucket in bucket_names
        ],
        "oldest_last_scanned": oldest_last_scanned,
        "avg_scan_count": average,
    }


def scan_backlog_rows(session: Session) -> dict[str, Any]:
    return _scan_backlog_rows(session, datetime.now(timezone.utc))


def scan_backlog_endpoint(session: Session = Depends(get_session)) -> dict[str, Any]:
    return scan_backlog_rows(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    database_session = sessionmaker(bind=engine)()

    reference = datetime.now(timezone.utc).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    rows = [
        McpServerRegistry(
            server_id="never-scanned",
            last_scanned=None,
            scan_count=0,
            first_seen=reference,
        ),
        McpServerRegistry(
            server_id="two-days",
            last_scanned=reference.replace() - timezone.utc.utcoffset(reference)
            if reference.tzinfo is not None
            else reference,
            scan_count=1,
            first_seen=reference,
        ),
    ]
    rows[1].last_scanned = reference.replace() - (
        reference.utcoffset() or timezone.utc.utcoffset(reference)
    ) if reference.tzinfo is not None else reference
    rows[1].last_scanned = reference - __import__("datetime").timedelta(days=2)
    rows.extend(
        [
            McpServerRegistry(
                server_id="forty-five-days",
                last_scanned=reference - __import__("datetime").timedelta(days=45),
                scan_count=2,
                first_seen=reference,
            ),
            McpServerRegistry(
                server_id="two-hundred-days",
                last_scanned=reference - __import__("datetime").timedelta(days=200),
                scan_count=3,
                first_seen=reference,
            ),
            McpServerRegistry(
                server_id="three-days",
                last_scanned=reference - __import__("datetime").timedelta(days=3),
                scan_count=4,
                first_seen=reference,
            ),
        ]
    )

    for row in rows:
        database_session.add(row)
    database_session.commit()

    local_app = FastAPI()

    @local_app.get("/api/registry/scan-backlog")
    def local_endpoint(session: Session = Depends(get_session)):
        return scan_backlog_rows(session)

    def override_session():
        yield database_session

    local_app.dependency_overrides[get_session] = override_session
    output = scan_backlog_rows(database_session)
    assert output["total_servers"] == 5
    assert output["never_scanned"] == 1
    assert output["buckets"][0] == {"bucket": "0-7d", "count": 2}
    assert output["buckets"][2] == {"bucket": "31-90d", "count": 1}
    assert output["buckets"][3] == {"bucket": "90d+", "count": 1}
    assert isinstance(output["avg_scan_count"], float)

    print("PASS")