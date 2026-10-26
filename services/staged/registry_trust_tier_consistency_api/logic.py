from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_TRUSTED_TIERS = {"TRUSTED_GENERAL", "TRUSTED_RESEARCH"}
_HIGH_RISK_TIERS = {"HIGH_RISK_ISOLATED", "KNOWN_THREAT"}


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _tier(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().upper()


def _score(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_registry_trust_tier_consistency(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=30)

    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.trust_score,
        McpServerRegistry.risk_tier,
        McpServerRegistry.verdict,
        McpServerRegistry.confidence,
        McpServerRegistry.last_assessed,
    )
    rows = session.execute(statement).mappings().all()

    totals = {
        "servers": len(rows),
        "consistent": 0,
        "inconsistent": 0,
        "stale": 0,
        "missing_score": 0,
    }
    inconsistent_rows: list[dict[str, Any]] = []

    for row in rows:
        score = _score(row["trust_score"])
        tier = _tier(row["risk_tier"])
        assessed = _as_datetime(row["last_assessed"])

        if score is None:
            totals["missing_score"] += 1
            continue

        if assessed is not None and assessed < stale_cutoff:
            totals["stale"] += 1
            continue

        consistent = (
            score >= 75 and tier in _TRUSTED_TIERS
        ) or (
            score <= 45 and tier in _HIGH_RISK_TIERS
        )

        if consistent:
            totals["consistent"] += 1
            continue

        totals["inconsistent"] += 1
        inconsistent_rows.append(
            {
                "server_id": row["server_id"],
                "name": row["name"],
                "trust_score": row["trust_score"],
                "risk_tier": row["risk_tier"],
                "verdict": row["verdict"],
                "last_assessed": row["last_assessed"],
                "_sort_score": score,
            }
        )

    inconsistent_rows.sort(key=lambda item: item["_sort_score"], reverse=True)

    inconsistent = []
    for item in inconsistent_rows[:100]:
        inconsistent.append(
            {
                "server_id": item["server_id"],
                "name": item["name"],
                "trust_score": item["trust_score"],
                "risk_tier": item["risk_tier"],
                "verdict": item["verdict"],
                "last_assessed": item["last_assessed"],
            }
        )

    return {
        "generated_at": now.isoformat(),
        "totals": totals,
        "inconsistent": inconsistent,
    }


def get_trust_tier_consistency(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_registry_trust_tier_consistency(session=session)


if __name__ == "__main__":
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
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    test_rows = [
        {
            "server_id": "consistent-1",
            "name": "Consistent server",
            "trust_score": 90,
            "risk_tier": "TRUSTED_GENERAL",
            "verdict": "approved",
            "confidence": 0.95,
            "last_assessed": now - timedelta(days=2),
        },
        {
            "server_id": "inconsistent-1",
            "name": "Contradicting server",
            "trust_score": 50,
            "risk_tier": "TRUSTED_GENERAL",
            "verdict": "approved",
            "confidence": 0.85,
            "last_assessed": now - timedelta(days=2),
        },
        {
            "server_id": "missing-1",
            "name": "Missing score server",
            "trust_score": None,
            "risk_tier": "TRUSTED_RESEARCH",
            "verdict": "review",
            "confidence": 0.70,
            "last_assessed": now - timedelta(days=2),
        },
        {
            "server_id": "stale-1",
            "name": "Stale server",
            "trust_score": 95,
            "risk_tier": "TRUSTED_GENERAL",
            "verdict": "approved",
            "confidence": 0.90,
            "last_assessed": now - timedelta(days=31),
        },
        {
            "server_id": "recent-1",
            "name": "Recent server",
            "trust_score": 20,
            "risk_tier": "HIGH_RISK_ISOLATED",
            "verdict": "blocked",
            "confidence": 0.99,
            "last_assessed": now - timedelta(days=1),
        },
    ]

    with TestSession() as session:
        session.add_all([McpServerRegistry(**row) for row in test_rows])
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/registry/trust-tier-consistency")
    def test_endpoint(session: Session = Depends(get_session)):
        return get_registry_trust_tier_consistency(session=session)

    def override_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/trust-tier-consistency")
        assert response.status_code == 200
        payload = response.json()
        assert payload["totals"]["servers"] == 5
        assert payload["totals"]["inconsistent"] >= 1
        assert any(
            item["server_id"] == "inconsistent-1"
            for item in payload["inconsistent"]
        )

    print("PASS")