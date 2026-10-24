from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_BUCKETS = (
    ("never scanned", 0, 0),
    ("1", 1, 1),
    ("2-4", 2, 4),
    ("5-9", 5, 9),
    ("10-49", 10, 49),
    ("50+", 50, None),
)


def _bucket_expression():
    scan_count = func.coalesce(McpServerRegistry.scan_count, 0)
    return case(
        (scan_count == 0, 0),
        (scan_count == 1, 1),
        (scan_count.between(2, 4), 2),
        (scan_count.between(5, 9), 3),
        (scan_count.between(10, 49), 4),
        else_=5,
    )


def get_scan_count_buckets(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    bucket_expression = _bucket_expression()

    bucket_rows = session.execute(
        select(bucket_expression.label("bucket"), func.count().label("count"))
        .select_from(McpServerRegistry)
        .group_by(bucket_expression)
    ).all()

    counts = {bucket: 0 for bucket in range(len(_BUCKETS))}
    for row in bucket_rows:
        counts[int(row.bucket)] = int(row.count)

    total = sum(counts.values())

    source_rows = session.execute(
        select(
            func.coalesce(McpServerRegistry.registry_source, "unknown").label(
                "registry_source"
            ),
            func.count().label("count"),
        )
        .select_from(McpServerRegistry)
        .group_by(func.coalesce(McpServerRegistry.registry_source, "unknown"))
        .order_by(func.coalesce(McpServerRegistry.registry_source, "unknown"))
    ).all()

    buckets = []
    for index, (label, minimum, maximum) in enumerate(_BUCKETS):
        count = counts[index]
        buckets.append(
            {
                "label": label,
                "min": minimum,
                "max": maximum,
                "count": count,
                "share": float(count / total) if total else 0.0,
            }
        )

    return {
        "buckets": buckets,
        "total": total,
        "never_scanned": counts[0],
        "by_source": [
            {
                "registry_source": str(row.registry_source),
                "count": int(row.count),
            }
            for row in source_rows
        ],
    }


# Compatibility alias for routers that use the fully qualified service name.
get_registry_scan_count_buckets = get_scan_count_buckets


if __name__ == "__main__":
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
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-0",
                    name="Never scanned",
                    registry_source="seed",
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="server-1",
                    name="Once scanned",
                    registry_source="seed",
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="server-3",
                    name="Three scans",
                    registry_source="seed",
                    scan_count=3,
                ),
                McpServerRegistry(
                    server_id="server-60",
                    name="Frequently scanned",
                    registry_source="seed",
                    scan_count=60,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/scan-count-buckets")
    def scan_count_buckets_endpoint(session: Session = Depends(get_session)):
        return get_scan_count_buckets(session)

    response = TestClient(test_app).get("/api/registry/scan-count-buckets")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["never_scanned"] == 1
    assert sum(bucket["count"] for bucket in payload["buckets"]) == payload["total"]
    print("PASS")