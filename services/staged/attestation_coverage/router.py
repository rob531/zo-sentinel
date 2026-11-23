"""HTTP surface for attestation coverage and staleness."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from . import logic as _logic

router = APIRouter(prefix="/api/attestations", tags=["attestation-coverage"])


class SourceCoverage(BaseModel):
    source: str
    total: int
    attested: int
    coverage_pct: float


class AttestationCoverageResponse(BaseModel):
    total_registry: int
    attested_total: int
    uncovered_total: int
    coverage_pct: float
    stale_7d: int
    stale_30d: int
    by_source: list[SourceCoverage]


def _first_present(mapping: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _coverage(total: int, attested: int) -> float:
    if total <= 0:
        return 0.0
    return round((attested / total) * 100.0, 2)


def _compute_fallback(db: Session) -> dict[str, Any]:
    registry_rows = db.execute(select(McpServerRegistry)).scalars().all()
    registry = [
        {
            "server_id": row.server_id,
            "source": row.registry_source or "unknown",
        }
        for row in registry_rows
    ]

    attestations = Table(
        "mcp_attestations",
        MetaData(),
        autoload_with=db.get_bind(),
    )
    rows = db.execute(select(attestations)).mappings().all()

    latest: dict[str, tuple[datetime | None, str]] = {}
    source_servers: dict[str, set[str]] = {}
    for row in rows:
        server_id = _first_present(row, ("server_id", "mcp_server_id"))
        if server_id is None:
            continue
        source = _first_present(row, ("source", "attestation_source", "registry_source"))
        source = str(source or "unknown")
        timestamp = _as_datetime(
            _first_present(
                row,
                (
                    "attested_at",
                    "created_at",
                    "assessed_at",
                    "assessment_date",
                    "timestamp",
                ),
            )
        )
        previous = latest.get(str(server_id))
        if previous is None or (
            timestamp is not None
            and (previous[0] is None or timestamp > previous[0])
        ):
            latest[str(server_id)] = (timestamp, source)
        source_servers.setdefault(source, set()).add(str(server_id))

    registry_ids = {str(item["server_id"]) for item in registry}
    attested_ids = registry_ids.intersection(latest)
    now = datetime.now(timezone.utc)
    stale_7d = sum(
        1
        for server_id in attested_ids
        if latest[server_id][0] is None
        or latest[server_id][0] < now - timedelta(days=7)
    )
    stale_30d = sum(
        1
        for server_id in attested_ids
        if latest[server_id][0] is None
        or latest[server_id][0] < now - timedelta(days=30)
    )

    sources: dict[str, list[int]] = {}
    for item in registry:
        source = str(item["source"])
        values = sources.setdefault(source, [0, 0])
        values[0] += 1
        if str(item["server_id"]) in attested_ids and latest[str(item["server_id"])][1] == source:
            values[1] += 1

    by_source = [
        {
            "source": source,
            "total": values[0],
            "attested": values[1],
            "coverage_pct": _coverage(values[0], values[1]),
        }
        for source, values in sorted(sources.items())
    ]
    total_registry = len(registry)
    attested_total = len(attested_ids)
    return {
        "total_registry": total_registry,
        "attested_total": attested_total,
        "uncovered_total": total_registry - attested_total,
        "coverage_pct": _coverage(total_registry, attested_total),
        "stale_7d": stale_7d,
        "stale_30d": stale_30d,
        "by_source": by_source,
    }


def _normalize_result(result: Any) -> dict[str, Any]:
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    elif hasattr(result, "dict"):
        result = result.dict()
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], list):
        merged = dict(result[0])
        merged["by_source"] = result[1]
        result = merged
    if not isinstance(result, Mapping):
        raise TypeError("attestation coverage logic returned an invalid result")
    if "by_source" not in result and "source_coverage" in result:
        result = {**result, "by_source": result["source_coverage"]}
    return dict(result)


@router.get("/coverage", response_model=AttestationCoverageResponse)
def get_attestation_coverage(
    db: Session = Depends(get_session),
) -> AttestationCoverageResponse:
    logic_function = None
    for name in (
        "compute_attestation_coverage",
        "attestation_coverage",
        "get_attestation_coverage",
        "coverage_summary",
    ):
        candidate = getattr(_logic, name, None)
        if callable(candidate):
            logic_function = candidate
            break

    result = (
        _normalize_result(logic_function(db))
        if logic_function is not None
        else _compute_fallback(db)
    )
    return AttestationCoverageResponse.model_validate(result)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()
    attestations = Table(
        "mcp_attestations",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("server_id", String, nullable=False),
        Column("mcp_server_id", String, nullable=True),
        Column("source", String, nullable=True),
        Column("attestation_source", String, nullable=True),
        Column("attested_at", DateTime, nullable=True),
        Column("created_at", DateTime, nullable=True),
    )
    metadata.create_all(engine)
    McpServerRegistry.__table__.create(engine)

    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="registry-1",
                    name="recent-one",
                    registry_source="alpha",
                    risk_tier="low",
                    last_assessed=datetime.now(timezone.utc) - timedelta(days=1),
                ),
                McpServerRegistry(
                    server_id="registry-2",
                    name="recent-two",
                    registry_source="beta",
                    risk_tier="medium",
                    last_assessed=datetime.now(timezone.utc) - timedelta(days=2),
                ),
                McpServerRegistry(
                    server_id="registry-3",
                    name="stale-one",
                    registry_source="alpha",
                    risk_tier="high",
                    last_assessed=datetime.now(timezone.utc) - timedelta(days=45),
                ),
                McpServerRegistry(
                    server_id="registry-4",
                    name="uncovered",
                    registry_source="beta",
                    risk_tier="low",
                    last_assessed=None,
                ),
            ]
        )
        db.commit()

        now = datetime.now(timezone.utc)
        db.execute(
            insert(attestations),
            [
                {
                    "server_id": "registry-1",
                    "mcp_server_id": "registry-1",
                    "source": "alpha",
                    "attestation_source": "alpha",
                    "attested_at": now - timedelta(days=1),
                    "created_at": now - timedelta(days=1),
                },
                {
                    "server_id": "registry-2",
                    "mcp_server_id": "registry-2",
                    "source": "beta",
                    "attestation_source": "beta",
                    "attested_at": now - timedelta(days=2),
                    "created_at": now - timedelta(days=2),
                },
                {
                    "server_id": "registry-3",
                    "mcp_server_id": "registry-3",
                    "source": "alpha",
                    "attestation_source": "alpha",
                    "attested_at": now - timedelta(days=45),
                    "created_at": now - timedelta(days=45),
                },
            ],
        )
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with Session() as db:
            yield db

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/attestations/coverage")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["uncovered_total"] == 1, payload
    assert payload["coverage_pct"] == 75.0, payload
    assert payload["stale_30d"] == 1, payload
    print("PASS")


if __name__ == "__main__":
    _self_test()