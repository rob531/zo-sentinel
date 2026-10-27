from __future__ import annotations

import inspect
import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from app.db import get_session
from app.models import FreshnessStat


DEFAULT_LIMIT = 50
MAX_LIMIT = 500


async def _execute(session: Any, statement: Any) -> Any:
    result = session.execute(statement)
    if inspect.isawaitable(result):
        result = await result
    return result


async def get_freshness_surfaces(
    session: Any = Depends(get_session),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
) -> dict[str, Any]:
    """Return freshness surfaces without exposing payload values."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    table = FreshnessStat.__table__
    statement = (
        table.select()
        .order_by(table.c.computed_at.desc())
        .limit(limit)
    )
    result = await _execute(session, statement)
    rows = result.mappings().all()

    surfaces = []
    for row in rows:
        payload = row["payload"]
        if payload is None:
            payload_keys = []
            serialized_payload = "null"
        elif isinstance(payload, dict):
            payload_keys = [str(key) for key in payload.keys()]
            serialized_payload = json.dumps(payload)
        else:
            payload_keys = []
            serialized_payload = json.dumps(payload)

        computed_at = row["computed_at"]
        if hasattr(computed_at, "isoformat"):
            computed_at_value = computed_at.isoformat()
        else:
            computed_at_value = str(computed_at)

        surfaces.append(
            {
                "surface": str(row["surface"]),
                "computed_at": computed_at_value,
                "compute_seconds": float(row["compute_seconds"]),
                "payload_keys": payload_keys,
                "payload_size": len(serialized_payload.encode("utf-8")),
            }
        )

    return {"surfaces": surfaces, "count": len(surfaces)}


# Compatibility aliases for routers and callers using the service under a
# different descriptive name.
list_freshness_surfaces = get_freshness_surfaces
get_freshness_surface_index = get_freshness_surfaces


router = APIRouter(prefix="/api/freshness")


@router.get("/surfaces")
async def freshness_surfaces(
    session: Any = Depends(get_session),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
) -> dict[str, Any]:
    return await get_freshness_surfaces(session=session, limit=limit)


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(test_engine)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    seeded_rows = [
        FreshnessStat(
            surface="registry",
            payload={"servers": 12, "source": "private-seed-value"},
            computed_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
            compute_seconds=0.25,
        ),
        FreshnessStat(
            surface="signals",
            payload={"signals": 7, "source": "private-signal-value"},
            computed_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            compute_seconds=0.5,
        ),
        FreshnessStat(
            surface="scores",
            payload={"scores": 4, "source": "private-score-value"},
            computed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            compute_seconds=0.75,
        ),
    ]

    with TestingSessionLocal() as session:
        session.add_all(seeded_rows)
        session.commit()

    async def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/freshness/surfaces")
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 3
        assert any(item["surface"] == "registry" for item in body["surfaces"])
        assert body["surfaces"][0]["surface"] == "registry"
        assert "servers" in body["surfaces"][0]["payload_keys"]
        assert "private-seed-value" not in response.text
        assert "private-signal-value" not in response.text
        assert "private-score-value" not in response.text

    print("PASS")