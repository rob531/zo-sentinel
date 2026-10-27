from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def get_registry_source_scan_recency(
    db: Session = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    stale_condition = or_(
        McpServerRegistry.last_scanned.is_(None),
        McpServerRegistry.last_scanned < cutoff,
    )

    statement = select(
        McpServerRegistry.registry_source,
        func.count().label("server_count"),
        func.coalesce(
            func.sum(case((McpServerRegistry.last_scanned.is_(None), 1), else_=0)),
            0,
        ).label("never_scanned"),
        func.coalesce(
            func.sum(case((stale_condition, 1), else_=0)),
            0,
        ).label("stale_over_7d"),
        func.min(McpServerRegistry.last_scanned).label("oldest_last_scanned"),
        func.max(McpServerRegistry.last_scanned).label("newest_last_scanned"),
        func.count(func.distinct(McpServerRegistry.scan_count)).label(
            "distinct_scan_count_values"
        ),
    ).group_by(McpServerRegistry.registry_source)

    sources: list[dict[str, Any]] = []
    for row in db.execute(statement):
        server_count = int(row.server_count or 0)
        stale_over_7d = int(row.stale_over_7d or 0)
        sources.append(
            {
                "registry_source": (
                    str(row.registry_source) if row.registry_source is not None else ""
                ),
                "server_count": server_count,
                "never_scanned": int(row.never_scanned or 0),
                "stale_over_7d": stale_over_7d,
                "stale_fraction": (
                    float(stale_over_7d / server_count) if server_count else 0.0
                ),
                "oldest_last_scanned": _isoformat(row.oldest_last_scanned),
                "newest_last_scanned": _isoformat(row.newest_last_scanned),
            }
        )

    sources.sort(key=lambda item: (-item["server_count"], item["registry_source"]))
    return {"sources": sources}


get_source_scan_recency = get_registry_source_scan_recency


@router.get("/registry/source-scan-recency")
def registry_source_scan_recency(
    db: Session = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    return get_registry_source_scan_recency(db)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with testing_session() as session:
        session.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, description, registry_source,
                     last_scanned, scan_count)
                VALUES
                    (:server_id, :name, :description, :registry_source,
                     :last_scanned, :scan_count)
                """
            ),
            [
                {
                    "server_id": "self-test-null",
                    "name": "Self Test Null",
                    "description": "Self-test registry entry",
                    "registry_source": "alpha",
                    "last_scanned": None,
                    "scan_count": 0,
                },
                {
                    "server_id": "self-test-old",
                    "name": "Self Test Old",
                    "description": "Self-test registry entry",
                    "registry_source": "alpha",
                    "last_scanned": now - timedelta(days=30),
                    "scan_count": 7,
                },
                {
                    "server_id": "self-test-recent",
                    "name": "Self Test Recent",
                    "description": "Self-test registry entry",
                    "registry_source": "beta",
                    "last_scanned": now - timedelta(days=1),
                    "scan_count": 2,
                },
            ],
        )
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/source-scan-recency")
        assert response.status_code == 200
        payload = response.json()
        assert sum(item["server_count"] for item in payload["sources"]) == 3
        assert sum(item["never_scanned"] for item in payload["sources"]) == 1
        assert sum(item["stale_over_7d"] for item in payload["sources"]) == 2
        assert all(
            0.0 <= item["stale_fraction"] <= 1.0 for item in payload["sources"]
        )

    print("PASS")


if __name__ == "__main__":
    _self_test()