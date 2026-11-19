"""HTTP surface for threat reference coverage."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, ThreatIntelRef

try:
    from .logic import get_threat_ref_coverage as _get_threat_ref_coverage
except ImportError:
    _get_threat_ref_coverage = None

router = APIRouter(prefix="/api", tags=["threat-refs"])


class CoverageResponse(BaseModel):
    window_days: int
    total_refs: int
    by_indicator_type: dict[str, int]
    by_source: dict[str, int]
    aggregator_count: int
    pulses: list[dict[str, Any]]
    coverage_ratio: float


def _compute_coverage(db: Session, window_days: int) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    cutoff_naive = cutoff.replace(tzinfo=None)

    refs = list(db.scalars(select(ThreatIntelRef)).all())
    windowed_refs: list[ThreatIntelRef] = []
    for ref in refs:
        fetched_at = ref.fetched_at
        if fetched_at is None:
            continue
        if fetched_at.tzinfo is not None:
            fetched_at = fetched_at.astimezone(timezone.utc).replace(tzinfo=None)
        if fetched_at >= cutoff_naive:
            windowed_refs.append(ref)

    registry = list(db.scalars(select(McpServerRegistry)).all())

    eligible_servers: dict[str, McpServerRegistry] = {}
    for server in registry:
        risk_tier = server.risk_tier
        if risk_tier is None or not str(risk_tier).strip():
            continue
        eligible_servers[server.server_id] = server

    matched_servers: set[str] = set()
    by_indicator_type: dict[str, int] = {}
    by_source: dict[str, int] = {}
    pulses: dict[str, dict[str, Any]] = {}

    for ref in windowed_refs:
        indicator_type = str(ref.indicator_type)
        source = str(ref.source)
        by_indicator_type[indicator_type] = by_indicator_type.get(indicator_type, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1

        pulse_id = str(ref.pulse_id)
        pulse = pulses.setdefault(
            pulse_id,
            {
                "pulse_id": pulse_id,
                "pulse_name": str(ref.pulse_name or ""),
                "ref_count": 0,
                "latest_pulse_created": "",
            },
        )
        pulse["ref_count"] += 1

        pulse_created = ref.pulse_created
        if pulse_created is not None:
            pulse_created_text = (
                pulse_created.isoformat()
                if isinstance(pulse_created, datetime)
                else str(pulse_created)
            )
            if pulse_created_text > pulse["latest_pulse_created"]:
                pulse["latest_pulse_created"] = pulse_created_text

        for server in registry:
            if (
                server.server_id == ref.indicator_value
                or server.url == ref.indicator_value
            ):
                if server.server_id in eligible_servers:
                    matched_servers.add(server.server_id)

    denominator = len(eligible_servers)
    coverage_ratio = (
        float(len(matched_servers)) / float(denominator) if denominator else 0.0
    )

    return {
        "window_days": window_days,
        "total_refs": len(windowed_refs),
        "by_indicator_type": dict(sorted(by_indicator_type.items())),
        "by_source": dict(sorted(by_source.items())),
        "aggregator_count": sum(1 for ref in windowed_refs if ref.is_aggregator),
        "pulses": [pulses[pulse_id] for pulse_id in sorted(pulses)],
        "coverage_ratio": coverage_ratio,
    }


@router.get("/threat-refs/coverage", response_model=CoverageResponse)
def get_threat_ref_coverage_route(
    window_days: int = 30,
    db: Session = Depends(get_session),
) -> CoverageResponse:
    if _get_threat_ref_coverage is not None:
        result = _get_threat_ref_coverage(db, window_days)
        if isinstance(result, dict):
            return CoverageResponse(**result)
        return result
    return CoverageResponse(**_compute_coverage(db, window_days))


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine, checkfirst=True)
    ThreatIntelRef.__table__.create(bind=engine, checkfirst=True)

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-alpha",
                    name="Alpha",
                    risk_tier="high",
                    url="https://alpha.example",
                ),
                McpServerRegistry(
                    server_id="server-beta",
                    name="Beta",
                    risk_tier="medium",
                    url="https://beta.example",
                ),
            ]
        )
        session.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="url",
                    indicator_value="https://alpha.example",
                    pulse_id="pulse-1",
                    pulse_name="Primary pulse",
                    pulse_created=now,
                    is_aggregator=False,
                    source="osint",
                    source_url="https://source.example/1",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="url",
                    indicator_value="server-beta",
                    pulse_id="pulse-1",
                    pulse_name="Primary pulse",
                    pulse_created=now,
                    is_aggregator=True,
                    source="osint",
                    source_url="https://source.example/2",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="domain",
                    indicator_value="unmatched.example",
                    pulse_id="pulse-1",
                    pulse_name="Primary pulse",
                    pulse_created=now,
                    is_aggregator=False,
                    source="feed",
                    source_url="https://source.example/3",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=4,
                    indicator_type="domain",
                    indicator_value="another-unmatched.example",
                    pulse_id="pulse-2",
                    pulse_name="Secondary pulse",
                    pulse_created=now,
                    is_aggregator=False,
                    source="feed",
                    source_url="https://source.example/4",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=5,
                    indicator_type="url",
                    indicator_value="https://alpha.example",
                    pulse_id="pulse-2",
                    pulse_name="Secondary pulse",
                    pulse_created=now,
                    is_aggregator=False,
                    source="osint",
                    source_url="https://source.example/5",
                    fetched_at=now,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/threat-refs/coverage")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_refs"] == 5
    assert payload["by_indicator_type"] == {"domain": 2, "url": 3}
    assert payload["by_source"] == {"feed": 2, "osint": 3}
    assert payload["aggregator_count"] == 1
    assert payload["pulses"]
    assert all("ref_count" in pulse for pulse in payload["pulses"])
    assert isinstance(payload["coverage_ratio"], float)
    assert 0.0 <= payload["coverage_ratio"] <= 1.0
    print("PASS")