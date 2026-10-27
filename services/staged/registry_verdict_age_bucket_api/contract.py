from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class VerdictAgeBucket(BaseModel):
    bucket: str
    count: int
    verdict_counts: Dict[str, int]
    avg_confidence: Optional[float]


class VerdictAgeResponse(BaseModel):
    generated_at: datetime
    total_servers: int
    buckets: List[VerdictAgeBucket]


router = APIRouter(prefix="/api")
app = FastAPI()
app.include_router(router)


def _bucket_for_age(age: timedelta) -> str:
    if age < timedelta(days=1):
        return "0-1d"
    if age < timedelta(days=3):
        return "1-3d"
    if age < timedelta(days=7):
        return "3-7d"
    if age < timedelta(days=14):
        return "7-14d"
    if age < timedelta(days=30):
        return "14-30d"
    return "30d+"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@router.get("/registry/verdict-age", response_model=VerdictAgeResponse)
def get_verdict_age(session: Session = Depends(get_session)) -> VerdictAgeResponse:
    now = datetime.now(timezone.utc)
    rows = session.execute(
        select(
            McpServerRegistry.verdict,
            McpServerRegistry.last_assessed,
            McpServerRegistry.confidence,
        )
    ).all()

    grouped = {
        name: {"count": 0, "verdicts": Counter(), "confidence": []}
        for name in ("0-1d", "1-3d", "3-7d", "7-14d", "14-30d", "30d+", "never")
    }

    for verdict, last_assessed, confidence in rows:
        if last_assessed is None:
            bucket = "never"
        else:
            assessed = _as_utc(last_assessed)
            age = max(now - assessed, timedelta(0))
            bucket = _bucket_for_age(age)

        grouped[bucket]["count"] += 1
        if verdict is not None:
            grouped[bucket]["verdicts"][str(verdict)] += 1
        if confidence is not None:
            grouped[bucket]["confidence"].append(float(confidence))

    buckets = []
    for name in ("0-1d", "1-3d", "3-7d", "7-14d", "14-30d", "30d+", "never"):
        values = grouped[name]
        confidence_values = values["confidence"]
        average = (
            sum(confidence_values) / len(confidence_values)
            if confidence_values
            else None
        )
        buckets.append(
            VerdictAgeBucket(
                bucket=name,
                count=values["count"],
                verdict_counts=dict(values["verdicts"]),
                avg_confidence=average,
            )
        )

    return VerdictAgeResponse(
        generated_at=now,
        total_servers=len(rows),
        buckets=buckets,
    )


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)

    testing_session = None
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    testing_session = SessionLocal()

    now = datetime.now(timezone.utc)
    testing_session.execute(
        McpServerRegistry.__table__.insert(),
        [
            {
                "server_id": "contract-server-0d",
                "verdict": "TRUSTED_GENERAL",
                "last_assessed": now - timedelta(days=0),
                "last_seen": now,
                "risk_tier": "LOW",
                "confidence": 0.95,
            },
            {
                "server_id": "contract-server-2d",
                "verdict": "TRUSTED_SPECIALIZED",
                "last_assessed": now - timedelta(days=2),
                "last_seen": now,
                "risk_tier": "MEDIUM",
                "confidence": 0.85,
            },
            {
                "server_id": "contract-server-10d",
                "verdict": "NEEDS_REVIEW",
                "last_assessed": now - timedelta(days=10),
                "last_seen": now,
                "risk_tier": "HIGH",
                "confidence": 0.60,
            },
            {
                "server_id": "contract-server-never",
                "verdict": "UNKNOWN",
                "last_assessed": None,
                "last_seen": now,
                "risk_tier": "UNKNOWN",
                "confidence": 0.0,
            },
        ],
    )
    testing_session.commit()

    def override_get_session():
        return testing_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/registry/verdict-age")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total_servers"] == 4, payload

            buckets = {item["bucket"]: item for item in payload["buckets"]}
            assert buckets["0-1d"]["count"] == 1, payload
            assert "never" in buckets, payload
            assert buckets["never"]["count"] == 1, payload
    finally:
        testing_session.close()
        engine.dispose()


if __name__ == "__main__":
    run()
    print("PASS")