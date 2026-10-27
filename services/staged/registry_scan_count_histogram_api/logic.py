from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends
from sqlalchemy import Integer, case, cast, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_BUCKET_ORDER = ("0", "1", "2-3", "4-9", "10+")
_BUCKET_BY_INDEX = {
    0: "0",
    1: "1",
    2: "2-3",
    3: "4-9",
    4: "10+",
}


def _default_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=7)


def get_scan_count_histogram(
    session: Session = Depends(get_session),
    cutoff: Optional[datetime] = None,
) -> dict:
    effective_cutoff = cutoff if cutoff is not None else _default_cutoff()
    normalized_scan_count = func.coalesce(
        cast(McpServerRegistry.scan_count, Integer), 0
    )
    bucket_index = case(
        (normalized_scan_count == 0, 0),
        (normalized_scan_count == 1, 1),
        (normalized_scan_count.between(2, 3), 2),
        (normalized_scan_count.between(4, 9), 3),
        else_=4,
    )

    total_servers = int(
        session.scalar(select(func.count()).select_from(McpServerRegistry)) or 0
    )

    bucket_rows = session.execute(
        select(
            bucket_index.label("bucket"),
            func.count().label("server_count"),
        )
        .select_from(McpServerRegistry)
        .group_by(bucket_index)
    ).all()
    counts_by_bucket = {
        _BUCKET_BY_INDEX[int(row.bucket)]: int(row.server_count)
        for row in bucket_rows
    }

    never_scanned_condition = or_(
        McpServerRegistry.scan_count == 0,
        McpServerRegistry.last_scanned.is_(None),
    )
    never_scanned_count = int(
        session.scalar(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(never_scanned_condition)
        )
        or 0
    )
    never_scanned_sample = list(
        session.scalars(
            select(McpServerRegistry.server_id)
            .where(never_scanned_condition)
            .order_by(McpServerRegistry.server_id)
            .limit(100)
        ).all()
    )

    stale_scan_count = int(
        session.scalar(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(McpServerRegistry.last_scanned < effective_cutoff)
        )
        or 0
    )

    buckets = []
    for bucket in _BUCKET_ORDER:
        count = counts_by_bucket.get(bucket, 0)
        buckets.append(
            {
                "bucket": bucket,
                "count": count,
                "pct": round((count / total_servers) * 100, 2)
                if total_servers
                else 0.0,
            }
        )

    return {
        "total_servers": total_servers,
        "buckets": buckets,
        "never_scanned_count": never_scanned_count,
        "never_scanned_sample": never_scanned_sample,
        "stale_scan_count": stale_scan_count,
    }


def get_registry_scan_count_histogram(
    session: Session = Depends(get_session),
    cutoff: Optional[datetime] = None,
) -> dict:
    return get_scan_count_histogram(session=session, cutoff=cutoff)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    McpServerRegistry.metadata.create_all(engine)

    now = datetime.now(timezone.utc)
    rows = [
        McpServerRegistry(
            server_id="registry-zero",
            name="Registry Zero",
            scan_count=0,
            risk_tier="low",
            last_scanned=now,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="registry-one",
            name="Registry One",
            scan_count=1,
            risk_tier="low",
            last_scanned=now,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="registry-two",
            name="Registry Two",
            scan_count=2,
            risk_tier="medium",
            last_scanned=now,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="registry-five",
            name="Registry Five",
            scan_count=5,
            risk_tier="medium",
            last_scanned=now - timedelta(days=365),
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="registry-twelve",
            name="Registry Twelve",
            scan_count=12,
            risk_tier="high",
            last_scanned=now,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="registry-none",
            name="Registry None",
            scan_count=None,
            risk_tier="unknown",
            last_scanned=None,
            last_seen=now,
        ),
    ]

    with testing_session() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()

    @that_app.get("/api/registry/scan-count-histogram")
    def scan_count_histogram(session: Session = Depends(get_session)):
        return get_scan_count_histogram(session=session)

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/scan-count-histogram")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 6
        assert payload["never_scanned_count"] == 2
        assert any(
            bucket["bucket"] == "10+" and bucket["count"] == 1
            for bucket in payload["buckets"]
        )
        assert payload["stale_scan_count"] == 1

    print("PASS")


if __name__ == "__main__":
    _self_test()