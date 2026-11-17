from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


RISK_TIER_ORDER = (
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT",
)
_RISK_TIER_RANK = {tier: index for index, tier in enumerate(RISK_TIER_ORDER)}


def _assessment_age_days(value: datetime | None, now: datetime) -> int | None:
    if value is None:
        return None

    assessed_at = value
    if assessed_at.tzinfo is None:
        assessed_at = assessed_at.replace(tzinfo=timezone.utc)
    else:
        assessed_at = assessed_at.astimezone(timezone.utc)

    return max(0, int((now - assessed_at).total_seconds() // 86400))


def get_risk_tier_freshness(session: Session = Depends(get_session)) -> dict[str, Any]:
    now = datetime.now(timezone.utc)

    tier_statement = (
        select(
            McpServerRegistry.risk_tier.label("risk_tier"),
            func.count().label("server_count"),
            func.sum(
                case(
                    (McpServerRegistry.last_assessed.is_(None), 1),
                    else_=0,
                )
            ).label("never_assessed"),
            func.min(McpServerRegistry.last_assessed).label("oldest_assessed_at"),
            func.max(McpServerRegistry.last_assessed).label("newest_assessed_at"),
        )
        .group_by(McpServerRegistry.risk_tier)
    )

    tier_rows = session.execute(tier_statement).all()
    by_risk_tier: list[dict[str, Any]] = []

    for row in tier_rows:
        raw_tier = row.risk_tier
        risk_tier = "UNKNOWN" if raw_tier is None else str(raw_tier)
        by_risk_tier.append(
            {
                "risk_tier": risk_tier,
                "count": int(row.server_count or 0),
                "never_assessed": int(row.never_assessed or 0),
                "oldest_age_days": _assessment_age_days(row.oldest_assessed_at, now),
                "newest_age_days": _assessment_age_days(row.newest_assessed_at, now),
            }
        )

    by_risk_tier.sort(
        key=lambda item: (
            _RISK_TIER_RANK.get(item["risk_tier"], len(RISK_TIER_ORDER)),
            item["risk_tier"],
        )
    )

    source_statement = (
        select(
            McpServerRegistry.registry_source.label("registry_source"),
            func.count().label("server_count"),
        )
        .group_by(McpServerRegistry.registry_source)
    )
    source_rows = session.execute(source_statement).all()

    sources = [
        {
            "registry_source": (
                "UNKNOWN" if row.registry_source is None else str(row.registry_source)
            ),
            "count": int(row.server_count or 0),
        }
        for row in source_rows
    ]
    sources.sort(key=lambda item: item["registry_source"])

    return {
        "total_servers": sum(item["count"] for item in by_risk_tier),
        "never_assessed": sum(item["never_assessed"] for item in by_risk_tier),
        "by_risk_tier": by_risk_tier,
        "sources": sources,
    }


def get_registry_risk_tier_freshness(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_risk_tier_freshness(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    McpServerRegistry.__table__.create(test_engine)
    now = datetime.now(timezone.utc)
    seed_rows = [
        {
            "server_id": "server-trusted-null",
            "name": "Trusted Null",
            "description": "Contract seed",
            "url": "https://example.invalid/null",
            "registry_source": "github",
            "trust_score": 90.0,
            "verdict": "TRUSTED",
            "verdict_reasoning": "Contract seed",
            "confidence": 0.95,
            "last_assessed": None,
            "first_seen": now,
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 1,
            "risk_tier": "TRUSTED_GENERAL",
            "meta": {},
        },
        {
            "server_id": "server-trusted-old",
            "name": "Trusted Old",
            "description": "Contract seed",
            "url": "https://example.invalid/old",
            "registry_source": "github",
            "trust_score": 90.0,
            "verdict": "TRUSTED",
            "verdict_reasoning": "Contract seed",
            "confidence": 0.95,
            "last_assessed": now.replace(microsecond=0) - __import__("datetime").timedelta(days=10),
            "first_seen": now,
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 1,
            "risk_tier": "TRUSTED_GENERAL",
            "meta": {},
        },
        {
            "server_id": "server-trusted-new",
            "name": "Trusted New",
            "description": "Contract seed",
            "url": "https://example.invalid/new",
            "registry_source": "internal",
            "trust_score": 90.0,
            "verdict": "TRUSTED",
            "verdict_reasoning": "Contract seed",
            "confidence": 0.95,
            "last_assessed": now,
            "first_seen": now,
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 1,
            "risk_tier": "TRUSTED_GENERAL",
            "meta": {},
        },
        {
            "server_id": "server-research-one",
            "name": "Research One",
            "description": "Contract seed",
            "url": "https://example.invalid/research-one",
            "registry_source": "github",
            "trust_score": 85.0,
            "verdict": "TRUSTED",
            "verdict_reasoning": "Contract seed",
            "confidence": 0.90,
            "last_assessed": now,
            "first_seen": now,
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 1,
            "risk_tier": "TRUSTED_RESEARCH",
            "meta": {},
        },
        {
            "server_id": "server-research-two",
            "name": "Research Two",
            "description": "Contract seed",
            "url": "https://example.invalid/research-two",
            "registry_source": "internal",
            "trust_score": 85.0,
            "verdict": "TRUSTED",
            "verdict_reasoning": "Contract seed",
            "confidence": 0.90,
            "last_assessed": now,
            "first_seen": now,
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 1,
            "risk_tier": "TRUSTED_RESEARCH",
            "meta": {},
        },
    ]

    with TestSession() as seed_session:
        seed_session.execute(insert(McpServerRegistry.__table__), seed_rows)
        seed_session.commit()

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/risk-tier-freshness")
    def risk_tier_freshness_endpoint(session: Session = Depends(get_session)):
        return get_risk_tier_freshness(session)

    async def perform_request():
        messages: list[dict[str, Any]] = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/registry/risk-tier-freshness",
            "raw_path": b"/api/registry/risk-tier-freshness",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
        await that_app(scope, receive, send)
        status = next(
            message["status"] for message in messages if message["type"] == "http.response.start"
        )
        body = b"".join(
            message.get("body", b"") for message in messages if message["type"] == "http.response.body"
        )
        return status, json.loads(body)

    status_code, payload = asyncio.run(perform_request())
    assert status_code == 200
    assert payload["total_servers"] == 5
    assert payload["never_assessed"] == 1

    tiers = {item["risk_tier"]: item for item in payload["by_risk_tier"]}
    assert tiers["TRUSTED_GENERAL"]["count"] == 3
    assert tiers["TRUSTED_GENERAL"]["never_assessed"] == 1
    assert tiers["TRUSTED_GENERAL"]["oldest_age_days"] >= 9
    assert tiers["TRUSTED_RESEARCH"]["count"] == 2
    assert len(payload["sources"]) == 2

    print("PASS")