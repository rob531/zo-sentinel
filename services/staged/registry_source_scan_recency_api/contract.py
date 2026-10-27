from datetime import datetime, timedelta, timezone
from typing import Iterator

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import case, cast, distinct, func, or_, select
from sqlalchemy.engine import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


def _isoformat(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@router.get("/registry/source-scan-recency")
def source_scan_recency(session: Session = Depends(get_session)) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    stale = case(
        (McpServerRegistry.last_scanned.is_(None), 1),
        (McpServerRegistry.last_scanned < cutoff, 1),
        else_=0,
    )
    server_count = func.count()
    distinct_scan_count = func.count(distinct(McpServerRegistry.scan_count))

    rows = session.execute(
        select(
            McpServerRegistry.registry_source,
            server_count.label("server_count"),
            distinct_scan_count.label("distinct_scan_count"),
            func.sum(stale).label("stale_over_7d"),
            func.min(McpServerRegistry.last_scanned).label("oldest_last_scanned"),
            func.max(McpServerRegistry.last_scanned).label("newest_last_scanned"),
        )
        .group_by(McpServerRegistry.registry_source)
        .order_by(server_count.desc(), McpServerRegistry.registry_source.asc())
    ).all()

    sources = []
    for registry_source, count, scan_values, stale_count, oldest, newest in rows:
        count = int(count)
        stale_count = int(stale_count or 0)
        sources.append(
            {
                "registry_source": str(registry_source or "unknown"),
                "server_count": count,
                "distinct_scan_count": int(scan_values or 0),
                "never_scanned": 0,
                "stale_over_7d": stale_count,
                "stale_fraction": float(stale_count / count) if count else 0.0,
                "oldest_last_scanned": _isoformat(oldest),
                "newest_last_scanned": _isoformat(newest),
            }
        )

    never_scanned_rows = session.execute(
        select(McpServerRegistry.registry_source)
        .where(McpServerRegistry.last_scanned.is_(None))
    ).all()
    never_scanned_by_source: dict[str, int] = {}
    for (registry_source,) in never_scanned_rows:
        key = str(registry_source or "unknown")
        never_scanned_by_source[key] = never_scanned_by_source.get(key, 0) + 1
    for source in sources:
        source["never_scanned"] = never_scanned_by_source.get(source["registry_source"], 0)

    return {"sources": sources}


app = FastAPI(title="Registry Source Scan Recency API")
app.include_router(router)


def _run_self_test() -> None:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    test_session_factory = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_session() -> Iterator[Session]:
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with test_session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="recency-mixed-1",
                    name="Mixed One",
                    registry_source="mixed",
                    url="https://example.test/mixed-1",
                    first_seen=now - timedelta(days=40),
                    last_seen=now,
                    last_assessed=now,
                    last_scanned=None,
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="recency-mixed-2",
                    name="Mixed Two",
                    registry_source="mixed",
                    url="https://example.test/mixed-2",
                    first_seen=now - timedelta(days=40),
                    last_seen=now,
                    last_assessed=now,
                    last_scanned=now - timedelta(days=30),
                    scan_count=7,
                ),
                McpServerRegistry(
                    server_id="recency-recent-1",
                    name="Recent One",
                    registry_source="official",
                    url="https://example.test/recent-1",
                    first_seen=now - timedelta(days=5),
                    last_seen=now,
                    last_assessed=now,
                    last_scanned=now - timedelta(hours=2),
                    scan_count=3,
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/registry/source-scan-recency")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert set(payload) == {"sources"}
            assert sum(item["server_count"] for item in payload["sources"]) == 3
            assert sum(item["never_scanned"] for item in payload["sources"]) == 1
            assert sum(item["stale_over_7d"] for item in payload["sources"]) == 2
            by_source = {item["registry_source"]: item for item in payload["sources"]}
            assert by_source["mixed"]["server_count"] == 2
            assert by_source["mixed"]["never_scanned"] == 1
            assert by_source["mixed"]["stale_over_7d"] == 2
            assert by_source["official"]["server_count"] == 1
            assert by_source["official"]["never_scanned"] == 0
            assert by_source["official"]["stale_over_7d"] == 0
            assert all(0.0 <= item["stale_fraction"] <= 1.0 for item in payload["sources"])
            assert by_source["mixed"]["oldest_last_scanned"] is not None
            assert by_source["mixed"]["newest_last_scanned"] is not None
    finally:
        test_engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()