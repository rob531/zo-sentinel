from datetime import datetime, timedelta
from typing import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import FreshnessStat
from services.staged.freshness_surface_age.router import router


def main() -> int:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    computed_at_column = FreshnessStat.__table__.c.computed_at
    original_nullable = computed_at_column.nullable
    computed_at_column.nullable = True
    try:
        FreshnessStat.__table__.create(engine)
    finally:
        computed_at_column.nullable = original_nullable

    now = datetime.utcnow()
    rows = [
        {
            "surface": "fresh_surface",
            "payload": '{"state": "fresh"}',
            "computed_at": (now - timedelta(minutes=5)).isoformat(sep=" "),
            "compute_seconds": 0.25,
        },
        {
            "surface": "old_surface",
            "payload": '{"state": "old"}',
            "computed_at": (now - timedelta(hours=2)).isoformat(sep=" "),
            "compute_seconds": 1.5,
        },
        {
            "surface": "null_computed_at_surface",
            "payload": '{"state": "unknown"}',
            "computed_at": None,
            "compute_seconds": 0.75,
        },
    ]

    with SessionLocal() as session:
        session.execute(
            text(
                """
                INSERT INTO freshness_stats
                    (surface, payload, computed_at, compute_seconds)
                VALUES
                    (:surface, :payload, :computed_at, :compute_seconds)
                """
            ),
            rows,
        )
        session.commit()

    def override_get_session() -> Iterator[Session]:
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/freshness/surfaces")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["surface_count"] == 3, body

        surfaces = body["surfaces"]
        null_rows = [
            row
            for row in surfaces
            if row["surface"] == "null_computed_at_surface"
        ]
        assert len(null_rows) == 1, surfaces
        assert null_rows[0]["age_seconds"] is None, null_rows[0]
        assert null_rows[0]["is_stale"] is True, null_rows[0]
        assert "old_surface" in body["stale_surfaces"], body
    finally:
        engine.dispose()

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())