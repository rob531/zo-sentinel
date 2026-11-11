from typing import Dict, List

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_scan_count_histogram(
    db: Session = Depends(get_session),
) -> List[Dict[str, object]]:
    scan_count = McpServerRegistry.scan_count
    bucket = case(
        (or_(scan_count.is_(None), scan_count <= 0), "0"),
        (scan_count == 1, "1"),
        (scan_count.between(2, 3), "2-3"),
        (scan_count.between(4, 9), "4-9"),
        (scan_count >= 10, "10+"),
        else_="0",
    )

    statement = (
        select(
            bucket.label("bucket"),
            func.count().label("count"),
            func.count(
                func.distinct(McpServerRegistry.registry_source)
            ).label("distinct_sources"),
        )
        .select_from(McpServerRegistry)
        .group_by(bucket)
    )

    grouped = {
        row["bucket"]: {
            "bucket": row["bucket"],
            "count": int(row["count"]),
            "pct": 0.0,
            "distinct_sources": int(row["distinct_sources"]),
        }
        for row in db.execute(statement).mappings()
    }

    total = sum(int(item["count"]) for item in grouped.values())
    ordered_labels = ("0", "1", "2-3", "4-9", "10+")
    buckets: List[Dict[str, object]] = []

    for label in ordered_labels:
        item = grouped.get(
            label,
            {
                "bucket": label,
                "count": 0,
                "pct": 0.0,
                "distinct_sources": 0,
            },
        )
        item["pct"] = (
            round((int(item["count"]) / total) * 100.0, 2) if total else 0.0
        )
        buckets.append(item)

    return buckets


def calculate_scan_count_histogram(
    db: Session = Depends(get_session),
) -> List[Dict[str, object]]:
    return get_scan_count_histogram(db)


def get_registry_scan_count_histogram(
    db: Session = Depends(get_session),
) -> List[Dict[str, object]]:
    return get_scan_count_histogram(db)


if __name__ == "__main__":
    from datetime import datetime, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    name="Server One",
                    registry_source="github",
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="Server Two",
                    registry_source="npm",
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="server-3",
                    name="Server Three",
                    registry_source="github",
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="server-4",
                    name="Server Four",
                    registry_source="npm",
                    scan_count=3,
                ),
                McpServerRegistry(
                    server_id="server-5",
                    name="Server Five",
                    registry_source="github",
                    scan_count=7,
                ),
                McpServerRegistry(
                    server_id="server-6",
                    name="Server Six",
                    registry_source="npm",
                    scan_count=25,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/scan-count-histogram")
    def scan_count_histogram(db: Session = Depends(get_session)):
        buckets = get_scan_count_histogram(db)
        return {
            "buckets": buckets,
            "total_servers": sum(int(bucket["count"]) for bucket in buckets),
            "generated_at": datetime.now(timezone.utc),
        }

    with TestClient(test_app) as client:
        response = client.get("/api/registry/scan-count-histogram")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["buckets"]) == 5
        assert sum(int(bucket["count"]) for bucket in payload["buckets"]) == 6
        assert payload["buckets"][0]["bucket"] == "0"
        assert payload["buckets"][0]["count"] == 2
        assert payload["total_servers"] == 6

    print("PASS")