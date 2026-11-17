from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=10)

    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="srv-never",
                name="Never scanned",
                registry_source="source-a",
                scan_count=0,
            ),
            McpServerRegistry(
                server_id="srv-old",
                name="Scanned ten days ago",
                registry_source="source-a",
                last_scanned=old,
                scan_count=1,
            ),
            McpServerRegistry(
                server_id="srv-today-a",
                name="Scanned today",
                registry_source="source-b",
                last_scanned=now,
                scan_count=2,
            ),
            McpServerRegistry(
                server_id="srv-today-b",
                name="Scanned today too",
                registry_source="source-b",
                last_scanned=now,
                scan_count=3,
            ),
        ]
    )
    session.commit()
    session.close()

    def override_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session

    client = TestClient(app)
    response = client.get("/api/registry/scan-coverage")
    assert response.status_code == 200, response.text

    body = response.json()
    sources = body["sources"]
    assert len(sources) == 2, body

    by_source = {row["registry_source"]: row for row in sources}
    source_a = by_source["source-a"]
    source_b = by_source["source-b"]

    assert source_a["never_scanned"] >= 1, body
    assert source_a["stale"] >= 1, body
    assert source_a["total"] == 2, body
    assert source_b["total"] == 2, body
    assert source_b["never_scanned"] == 0, body
    assert source_b["scanned_last_24h"] == 2, body

    sample_ids = {item["server_id"] for item in body["never_scanned_sample"]}
    assert "srv-never" in sample_ids, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)