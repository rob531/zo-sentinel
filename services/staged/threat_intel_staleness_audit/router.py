"""HTTP surface for threat intelligence staleness auditing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import *  # noqa: F401,F403
from . import logic as _logic

router = APIRouter(prefix="/api/threat-intel", tags=["threat-intel"])


class StalenessBucket(BaseModel):
    bucket: str
    count: int


class SourceStaleness(BaseModel):
    source: str
    count: int
    oldest_fetched_at: datetime | None = None
    newest_fetched_at: datetime | None = None
    inconsistent_count: int = 0


class StalenessResponse(BaseModel):
    total_rows: int
    buckets: list[StalenessBucket]
    by_source: list[SourceStaleness]
    generated_at: datetime


def _run_staleness_audit(db: Session) -> dict[str, Any]:
    for name in (
        "threat_intel_staleness_audit",
        "get_threat_intel_staleness",
        "staleness_audit",
        "get_staleness_audit",
        "staleness_report",
        "get_staleness_report",
        "build_staleness_report",
    ):
        function = getattr(_logic, name, None)
        if function is not None:
            report = function(db)
            if isinstance(report, dict):
                return report
            return dict(report)

    raise RuntimeError("logic.py does not expose a threat-intel staleness audit function")


def _normalize_buckets(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [{"bucket": str(bucket), "count": int(count)} for bucket, count in value.items()]
    return [
        {
            "bucket": str(item["bucket"]),
            "count": int(item["count"]),
        }
        for item in value or []
    ]


def _normalize_sources(value: Any) -> list[dict[str, Any]]:
    return [
        {
            "source": str(item["source"]),
            "count": int(item["count"]),
            "oldest_fetched_at": item.get("oldest_fetched_at"),
            "newest_fetched_at": item.get("newest_fetched_at"),
            "inconsistent_count": int(item.get("inconsistent_count", 0)),
        }
        for item in value or []
    ]


@router.get("/staleness", response_model=StalenessResponse)
def get_threat_intel_staleness(
    db: Session = Depends(get_session),
) -> StalenessResponse:
    report = _run_staleness_audit(db)
    buckets = _normalize_buckets(report.get("buckets", []))
    by_source = _normalize_sources(report.get("by_source", []))
    total_rows = int(
        report.get(
            "total_rows",
            sum(item["count"] for item in buckets),
        )
    )
    generated_at = report.get("generated_at") or datetime.now(timezone.utc)

    return StalenessResponse(
        total_rows=total_rows,
        buckets=[StalenessBucket(**item) for item in buckets],
        by_source=[SourceStaleness(**item) for item in by_source],
        generated_at=generated_at,
    )


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    ThreatIntelRef.__table__.create(engine)

    now = datetime.now(timezone.utc)
    rows = [
        ThreatIntelRef(
            id=1001,
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-fresh",
            pulse_name="Fresh pulse",
            pulse_created=now - timedelta(hours=2),
            is_aggregator=False,
            source="unit-test-source",
            source_url="https://example.test/fresh",
            fetched_at=now - timedelta(hours=1),
        ),
        ThreatIntelRef(
            id=1002,
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id="pulse-medium",
            pulse_name="Medium pulse",
            pulse_created=now - timedelta(days=2),
            is_aggregator=False,
            source="unit-test-source",
            source_url="https://example.test/medium",
            fetched_at=now - timedelta(days=2),
        ),
        ThreatIntelRef(
            id=1003,
            indicator_type="url",
            indicator_value="https://example.test/old",
            pulse_id="pulse-old",
            pulse_name="Old pulse",
            pulse_created=now - timedelta(days=3),
            is_aggregator=False,
            source="unit-test-source",
            source_url="https://example.test/old",
            fetched_at=now - timedelta(days=5),
        ),
        ThreatIntelRef(
            id=1004,
            indicator_type="hash",
            indicator_value="a" * 64,
            pulse_id="pulse-older",
            pulse_name="Older pulse",
            pulse_created=now - timedelta(days=10),
            is_aggregator=False,
            source="unit-test-source",
            source_url="https://example.test/older",
            fetched_at=now - timedelta(days=10),
        ),
        ThreatIntelRef(
            id=1005,
            indicator_type="ipv6",
            indicator_value="2001:db8::1",
            pulse_id="pulse-inconsistent",
            pulse_name="Inconsistent pulse",
            pulse_created=now + timedelta(hours=1),
            is_aggregator=False,
            source="unit-test-source",
            source_url="https://example.test/inconsistent",
            fetched_at=now - timedelta(days=1),
        ),
    ]

    with TestingSession() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/staleness")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert sum(bucket["count"] for bucket in payload["buckets"]) == len(rows)
        source_rows = [item for item in payload["by_source"] if item["source"] == "unit-test-source"]
        assert source_rows
        assert source_rows[0]["inconsistent_count"] >= 1

    print("PASS")