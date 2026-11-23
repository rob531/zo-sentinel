"""Logic for inspecting freshness payloads from the real application database."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


def get_freshness_payload(db: Session, surface: str) -> Optional[Dict[str, Any]]:
    """Return the latest freshness record for a surface, or ``None``."""
    row = db.execute(
        select(FreshnessStat).where(FreshnessStat.surface == surface)
    ).scalar_one_or_none()
    if row is None:
        return None

    computed_at = row.computed_at
    if computed_at.tzinfo is None:
        computed_at = computed_at.replace(tzinfo=timezone.utc)
    else:
        computed_at = computed_at.astimezone(timezone.utc)

    age_seconds = max(
        0,
        int((datetime.now(timezone.utc) - computed_at).total_seconds()),
    )

    return {
        "surface": row.surface,
        "computed_at": row.computed_at,
        "compute_seconds": row.compute_seconds,
        "age_seconds": age_seconds,
        "payload": row.payload,
    }


def get_freshness_stats(db: Session, surface: str) -> Optional[Dict[str, Any]]:
    """Compatibility alias for :func:`get_freshness_payload`."""
    return get_freshness_payload(db, surface)


def get_freshness(db: Session, surface: str) -> Optional[Dict[str, Any]]:
    """Compatibility alias for :func:`get_freshness_payload`."""
    return get_freshness_payload(db, surface)


if __name__ == "__main__":
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with SessionLocal() as db:
        db.add(
            FreshnessStat(
                surface="registry",
                payload={"server_count": 12, "generated": "now"},
                computed_at=datetime.now(timezone.utc),
                compute_seconds=0.25,
            )
        )
        db.add(
            FreshnessStat(
                surface="advisories",
                payload={"advisory_count": 4, "sources": ["feed-a", "feed-b"]},
                computed_at=datetime.now(timezone.utc),
                compute_seconds=0.5,
            )
        )
        db.commit()

    test_app = FastAPI()

    def override_get_session():
        with SessionLocal() as db:
            yield db

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/freshness/{surface}")
    def inspect_freshness(surface: str, db: Session = __import__("fastapi").Depends(get_session)):
        result = get_freshness_payload(db, surface)
        if result is None:
            raise HTTPException(status_code=404, detail="surface not found")
        return result

    client = TestClient(test_app)
    response = client.get("/api/freshness/registry")
    assert response.status_code == 200
    body = response.json()
    assert body["surface"] == "registry"
    assert body["payload"] == {"server_count": 12, "generated": "now"}
    assert isinstance(body["age_seconds"], int)
    assert body["age_seconds"] >= 0

    response = client.get("/api/freshness/missing")
    assert response.status_code == 404
    assert response.json() == {"detail": "surface not found"}

    print("PASS")