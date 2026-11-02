from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


FACET_KEYS = ("registry_source", "risk_tier", "verdict")

_FACET_COLUMNS = {
    "registry_source": McpServerRegistry.registry_source,
    "risk_tier": McpServerRegistry.risk_tier,
    "verdict": McpServerRegistry.verdict,
}


def _generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _total_server_count(session: Session) -> int:
    return int(session.execute(select(func.count()).select_from(McpServerRegistry)).scalar_one())


def _facet_rows(session: Session, facet: str) -> list[tuple[Any, int]]:
    column = _FACET_COLUMNS[facet]
    statement = (
        select(column, func.count())
        .where(column.isnot(None))
        .group_by(column)
        .order_by(func.count().desc(), column.asc())
    )
    return [(value, int(count)) for value, count in session.execute(statement).all()]


def get_registry_facets(session: Session) -> dict[str, Any]:
    total = _total_server_count(session)
    facets = {
        facet: [
            {"value": value, "count": count}
            for value, count in _facet_rows(session, facet)
        ]
        for facet in FACET_KEYS
    }
    return {
        "total": total,
        "facets": facets,
        "generated_at": _generated_at(),
    }


def get_facet_values(session: Session, facet: str, limit: int = 50) -> dict[str, Any]:
    if facet not in _FACET_COLUMNS:
        raise ValueError(f"Unknown registry facet: {facet}")

    normalized_limit = max(1, min(int(limit), 100))
    values = [
        {"value": value, "count": count}
        for value, count in _facet_rows(session, facet)[:normalized_limit]
    ]
    return {
        "facet": facet,
        "values": values,
        "total": _total_server_count(session),
        "generated_at": _generated_at(),
    }


def get_facets(session: Session) -> dict[str, Any]:
    return get_registry_facets(session)


def get_registry_facet_index(session: Session) -> dict[str, Any]:
    return get_registry_facets(session)


def list_facet_values(session: Session, facet: str, limit: int = 50) -> dict[str, Any]:
    return get_facet_values(session, facet, limit)


def compute_facet_buckets(session: Session) -> dict[str, Any]:
    return get_registry_facets(session)


def _self_test() -> None:
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
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seeded = [
        ("server-1", "community", "medium", "trusted"),
        ("server-2", "community", "low", "trusted"),
        ("server-3", "community", "low", "caution"),
        ("server-4", "official", "low", "trusted"),
        ("server-5", "official", "high", "blocked"),
        ("server-6", "official", "high", "blocked"),
    ]
    session = SessionLocal()
    try:
        for server_id, registry_source, risk_tier, verdict in seeded:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    registry_source=registry_source,
                    risk_tier=risk_tier,
                    verdict=verdict,
                )
            )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        override_session = SessionLocal()
        try:
            yield override_session
        finally:
            override_session.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/facets")
    def registry_facets(session: Session = Depends(get_session)):
        return get_registry_facets(session)

    @test_app.get("/api/registry/facets/{facet}/values")
    def registry_facet_values(
        facet: str,
        limit: int = 50,
        session: Session = Depends(get_session),
    ):
        return get_facet_values(session, facet, limit)

    with TestClient(test_app) as client:
        facets_response = client.get("/api/registry/facets")
        assert facets_response.status_code == 200
        facets_payload = facets_response.json()
        assert facets_payload["total"] == len(seeded)
        assert set(facets_payload["facets"]) == set(FACET_KEYS)
        assert {"value": "community", "count": 3} in facets_payload["facets"]["registry_source"]
        assert {"value": "official", "count": 3} in facets_payload["facets"]["registry_source"]
        assert {"value": "low", "count": 3} in facets_payload["facets"]["risk_tier"]
        assert {"value": "high", "count": 2} in facets_payload["facets"]["risk_tier"]

        values_response = client.get(
            "/api/registry/facets/registry_source/values",
            params={"limit": 50},
        )
        assert values_response.status_code == 200
        values_payload = values_response.json()
        assert values_payload["total"] == len(seeded)
        assert {"value": "community", "count": 3} in values_payload["values"]

    print("PASS")


if __name__ == "__main__":
    _self_test()