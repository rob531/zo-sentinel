from datetime import datetime, timedelta, timezone

from fastapi import Depends
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


BUCKET_ORDER = ("0-1d", "1-7d", "7-30d", "30-90d", "90d+")

_BUCKET_LABELS = {
    "0-1d": "0-1d",
    "1-7d": "1-7d",
    "7-30d": "7-30d",
    "30-90d": "30-90d",
    "90d+": "90d+",
}


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _bucket_expression(now: datetime):
    thresholds = (
        (now - timedelta(days=1), _BUCKET_LABELS["0-1d"]),
        (now - timedelta(days=7), _BUCKET_LABELS["1-7d"]),
        (now - timedelta(days=30), _BUCKET_LABELS["7-30d"]),
        (now - timedelta(days=90), _BUCKET_LABELS["30-90d"]),
    )

    expression = case(
        (McpServerRegistry.last_seen >= thresholds[0][0], thresholds[0][1]),
        (McpServerRegistry.last_seen >= thresholds[1][0], thresholds[1][1]),
        (McpServerRegistry.last_seen >= thresholds[2][0], thresholds[2][1]),
        (McpServerRegistry.last_seen >= thresholds[3][0], thresholds[3][1]),
        else_=_BUCKET_LABELS["90d+"],
    )
    return expression


def get_last_seen_staleness(
    session: Session = Depends(get_session),
) -> dict:
    now = datetime.now(timezone.utc)
    cutoff_now = now.replace(tzinfo=None)
    bucket_expression = _bucket_expression(cutoff_now)

    statement = select(
        McpServerRegistry.registry_source,
        bucket_expression.label("bucket"),
    )
    rows = session.execute(statement).all()

    counts = {bucket: 0 for bucket in BUCKET_ORDER}
    source_counts: dict[str, dict[str, int]] = {}

    for row in rows:
        bucket = row.bucket if row.bucket in counts else "90d+"
        counts[bucket] += 1

        source = row.registry_source
        if source is None:
            source = "unknown"
        else:
            source = str(source)

        if source not in source_counts:
            source_counts[source] = {name: 0 for name in BUCKET_ORDER}
        source_counts[source][bucket] += 1

    return {
        "as_of": now.isoformat(),
        "total_servers": len(rows),
        "buckets": [
            {"bucket": bucket, "count": counts[bucket]}
            for bucket in BUCKET_ORDER
        ],
        "by_source": [
            {
                "registry_source": source,
                "buckets": [
                    {"bucket": bucket, "count": source_counts[source][bucket]}
                    for bucket in BUCKET_ORDER
                ],
            }
            for source in sorted(source_counts)
        ],
    }


def get_registry_last_seen_staleness(
    session: Session = Depends(get_session),
) -> dict:
    return get_last_seen_staleness(session)


def get_staleness_data(
    session: Session = Depends(get_session),
) -> dict:
    return get_last_seen_staleness(session)


def compute_last_seen_staleness(
    session: Session = Depends(get_session),
) -> dict:
    return get_last_seen_staleness(session)


if __name__ == "__main__":
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
    McpServerRegistry.__table__.create(engine)
    test_session_factory = sessionmaker(bind=engine)

    with test_session_factory() as session:
        base = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add_all(
            [
                McpServerRegistry(
                    server_id="fresh",
                    name="fresh",
                    registry_source="alpha",
                    risk_tier="low",
                    verdict="approved",
                    first_seen=base - timedelta(days=2),
                    last_seen=base - timedelta(hours=2),
                    last_scanned=base - timedelta(hours=2),
                    scan_count=2,
                ),
                McpServerRegistry(
                    server_id="middle",
                    name="middle",
                    registry_source="beta",
                    risk_tier="medium",
                    verdict="review",
                    first_seen=base - timedelta(days=40),
                    last_seen=base - timedelta(days=10),
                    last_scanned=base - timedelta(days=10),
                    scan_count=3,
                ),
                McpServerRegistry(
                    server_id="old",
                    name="old",
                    registry_source="alpha",
                    risk_tier="high",
                    verdict="blocked",
                    first_seen=base - timedelta(days=120),
                    last_seen=base - timedelta(days=120),
                    last_scanned=base - timedelta(days=120),
                    scan_count=1,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/registry/last-seen-staleness")
    def test_endpoint(session: Session = Depends(get_session)):
        return get_last_seen_staleness(session)

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/last-seen-staleness")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 3
        assert sum(item["count"] for item in payload["buckets"]) == 3
        assert sum(
            item["count"]
            for source in payload["by_source"]
            for item in source["buckets"]
        ) == 3

    print("PASS")