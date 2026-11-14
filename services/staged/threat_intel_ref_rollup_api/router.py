"""HTTP surface for the threat intelligence reference rollup service."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

try:
    from .logic import compute_rollup as _logic_compute_rollup
except ImportError:
    try:
        from logic import compute_rollup as _logic_compute_rollup  # type: ignore
    except ImportError:
        _logic_compute_rollup = None

router = APIRouter(prefix="/api/threat-intel/refs", tags=["threat-intel"])


class IndicatorTypeCount(BaseModel):
    indicator_type: str
    count: int


class SourceCount(BaseModel):
    source: str
    count: int


class PulseCount(BaseModel):
    pulse_id: Any
    pulse_name: str | None = None
    count: int


class RollupResponse(BaseModel):
    total: int
    by_indicator_type: list[IndicatorTypeCount]
    by_source: list[SourceCount]
    pulses: list[PulseCount]
    aggregator_count: int
    non_aggregator_count: int
    last_fetched_at: datetime | None = None


def _local_rollup(db: Session) -> dict[str, Any]:
    rows = list(db.execute(select(ThreatIntelRef)).scalars())

    indicator_counts = Counter(row.indicator_type for row in rows)
    source_counts = Counter(row.source for row in rows)
    pulse_counts = Counter((row.pulse_id, row.pulse_name) for row in rows)

    aggregator_count = sum(1 for row in rows if bool(row.is_aggregator))
    fetched_values = [row.fetched_at for row in rows if row.fetched_at is not None]

    return {
        "total": len(rows),
        "by_indicator_type": [
            {"indicator_type": indicator_type, "count": count}
            for indicator_type, count in sorted(indicator_counts.items())
        ],
        "by_source": [
            {"source": source, "count": count}
            for source, count in sorted(source_counts.items())
        ],
        "pulses": [
            {
                "pulse_id": pulse_id,
                "pulse_name": pulse_name,
                "count": count,
            }
            for (pulse_id, pulse_name), count in sorted(
                pulse_counts.items(), key=lambda item: str(item[0][0])
            )
        ],
        "aggregator_count": aggregator_count,
        "non_aggregator_count": len(rows) - aggregator_count,
        "last_fetched_at": max(fetched_values) if fetched_values else None,
    }


def _normalize_rollup(raw: dict[str, Any]) -> dict[str, Any]:
    by_indicator_type = []
    for item in raw.get("by_indicator_type", []):
        if isinstance(item, dict):
            by_indicator_type.append(item)
        else:
            by_indicator_type.append(
                {"indicator_type": item[0], "count": item[1]}
            )

    by_source = []
    for item in raw.get("by_source", []):
        if isinstance(item, dict):
            by_source.append(item)
        else:
            by_source.append({"source": item[0], "count": item[1]})

    pulses = []
    for item in raw.get("pulses", []):
        if isinstance(item, dict):
            pulses.append(item)
        else:
            pulses.append(
                {"pulse_id": item[0], "pulse_name": item[1], "count": item[2]}
            )

    return {
        "total": raw.get("total", sum(item["count"] for item in by_indicator_type)),
        "by_indicator_type": by_indicator_type,
        "by_source": by_source,
        "pulses": pulses,
        "aggregator_count": raw.get("aggregator_count", 0),
        "non_aggregator_count": raw.get("non_aggregator_count", 0),
        "last_fetched_at": raw.get("last_fetched_at"),
    }


@router.get("/rollup", response_model=RollupResponse)
def get_ref_rollup(db: Session = Depends(get_session)) -> RollupResponse:
    raw = (
        _logic_compute_rollup(db)
        if _logic_compute_rollup is not None
        else _local_rollup(db)
    )
    return RollupResponse(**_normalize_rollup(raw))


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="ipv4",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-a",
                    pulse_name="Pulse A",
                    pulse_created=datetime(2024, 1, 1),
                    is_aggregator=True,
                    source="alpha",
                    source_url="https://example.invalid/a",
                    fetched_at=datetime(2024, 1, 2, 3, 4, 5),
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="ipv4",
                    indicator_value="192.0.2.2",
                    pulse_id="pulse-a",
                    pulse_name="Pulse A",
                    pulse_created=datetime(2024, 1, 1),
                    is_aggregator=False,
                    source="alpha",
                    source_url="https://example.invalid/b",
                    fetched_at=datetime(2024, 1, 3, 3, 4, 5),
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="domain",
                    indicator_value="example.invalid",
                    pulse_id="pulse-b",
                    pulse_name="Pulse B",
                    pulse_created=datetime(2024, 1, 2),
                    is_aggregator=False,
                    source="beta",
                    source_url="https://example.invalid/c",
                    fetched_at=datetime(2024, 1, 4, 3, 4, 5),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/threat-intel/refs/rollup")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert {"indicator_type": "ipv4", "count": 2} in payload["by_indicator_type"]
    assert payload["last_fetched_at"] is not None
    print("PASS")