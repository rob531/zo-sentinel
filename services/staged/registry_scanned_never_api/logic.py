from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_scanned_vs_never(session: Session = Depends(get_session)) -> dict[str, Any]:
    scan_bucket = case(
        (
            or_(
                McpServerRegistry.scan_count.is_(None),
                McpServerRegistry.scan_count == 0,
            ),
            "never_scanned",
        ),
        else_="scanned",
    )

    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.registry_source,
            scan_bucket.label("bucket"),
        ).order_by(
            McpServerRegistry.registry_source,
            scan_bucket,
            McpServerRegistry.server_id,
        )
    ).all()

    grouped: dict[tuple[Any, str], dict[str, Any]] = {}
    total_never_scanned = 0

    for server_id, registry_source, bucket in rows:
        key = (registry_source, bucket)
        entry = grouped.setdefault(
            key,
            {
                "registry_source": registry_source,
                "bucket": bucket,
                "count": 0,
                "sample": [],
            },
        )
        entry["count"] += 1
        if len(entry["sample"]) < 20:
            entry["sample"].append(server_id)
        if bucket == "never_scanned":
            total_never_scanned += 1

    buckets = sorted(
        grouped.values(),
        key=lambda item: (
            item["registry_source"] is None,
            item["registry_source"] or "",
            item["bucket"],
        ),
    )

    return {
        "buckets": buckets,
        "total_never_scanned": total_never_scanned,
    }


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)

    with Session(test_engine) as test_session:
        test_session.add_all(
            [
                McpServerRegistry(
                    server_id="server-null-scan",
                    name="Null scan",
                    registry_source="contract",
                    scan_count=None,
                ),
                McpServerRegistry(
                    server_id="server-zero-scan",
                    name="Zero scan",
                    registry_source="contract",
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="server-scanned",
                    name="Scanned",
                    registry_source="contract",
                    scan_count=3,
                ),
            ]
        )
        test_session.commit()

    def override_get_session():
        with Session(test_engine) as test_session:
            yield test_session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/scanned-vs-never")
    def scanned_vs_never(payload: dict[str, Any] = Depends(get_scanned_vs_never)):
        return payload

    response = TestClient(test_app).get("/api/registry/scanned-vs-never")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_never_scanned"] == 2
    assert any(bucket["count"] >= 1 for bucket in payload["buckets"])

    print("PASS")