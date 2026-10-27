from __future__ import annotations

from collections import OrderedDict
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


def compute_verdict_reasoning_gaps(session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.verdict,
            McpServerRegistry.verdict_reasoning,
            McpServerRegistry.confidence,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
        )
    ).all()

    groups: dict[str, dict[str, Any]] = {}

    for row in rows:
        verdict = row.verdict if row.verdict is not None else "UNKNOWN"
        group = groups.setdefault(
            verdict,
            {
                "verdict": verdict,
                "total": 0,
                "missing_reasoning": 0,
                "confidence_bands": {
                    "null": 0,
                    "low": 0,
                    "mid": 0,
                    "high": 0,
                },
            },
        )

        group["total"] += 1

        reasoning = row.verdict_reasoning
        if reasoning is None or (isinstance(reasoning, str) and not reasoning.strip()):
            group["missing_reasoning"] += 1

        confidence = row.confidence
        if confidence is None:
            group["confidence_bands"]["null"] += 1
        elif confidence < 0.5:
            group["confidence_bands"]["low"] += 1
        elif confidence < 0.8:
            group["confidence_bands"]["mid"] += 1
        else:
            group["confidence_bands"]["high"] += 1

    verdicts = []
    total_servers = 0
    total_missing = 0

    for verdict in sorted(groups):
        group = groups[verdict]
        total = group["total"]
        missing = group["missing_reasoning"]
        total_servers += total
        total_missing += missing

        verdicts.append(
            {
                "verdict": verdict,
                "total": total,
                "missing_reasoning": missing,
                "reasoning_coverage_pct": round(
                    ((total - missing) / total * 100.0) if total else 0.0,
                    1,
                ),
                "confidence_bands": group["confidence_bands"],
            }
        )

    return {
        "verdicts": verdicts,
        "totals": {
            "servers": total_servers,
            "missing_reasoning": total_missing,
        },
    }


def get_verdict_reasoning_gaps(session: Session = Depends(get_session)) -> dict[str, Any]:
    return compute_verdict_reasoning_gaps(session)


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    test_session = TestSession()
    test_session.add_all(
        [
            McpServerRegistry(
                server_id="server-1",
                name="Server 1",
                verdict="TRUSTED_GENERAL",
                verdict_reasoning="Known publisher and maintained project.",
                confidence=0.9,
            ),
            McpServerRegistry(
                server_id="server-2",
                name="Server 2",
                verdict="TRUSTED_GENERAL",
                verdict_reasoning="Verified source and clear ownership.",
                confidence=0.7,
            ),
            McpServerRegistry(
                server_id="server-3",
                name="Server 3",
                verdict="TRUSTED_GENERAL",
                verdict_reasoning="",
                confidence=0.4,
            ),
            McpServerRegistry(
                server_id="server-4",
                name="Server 4",
                verdict="TRUSTED_GENERAL",
                verdict_reasoning=None,
                confidence=None,
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()

    @test_app.get("/api/registry/verdict-reasoning-gaps")
    def verdict_reasoning_gaps_endpoint(session: Session = Depends(get_session)):
        return get_verdict_reasoning_gaps(session=session)

    test_app.dependency_overrides[get_session] = lambda: test_session
    response = TestClient(test_app).get("/api/registry/verdict-reasoning-gaps")

    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["servers"] == 4
    assert payload["totals"]["missing_reasoning"] == 2
    assert payload["verdicts"][0]["verdict"] == "TRUSTED_GENERAL"
    assert payload["verdicts"][0]["total"] == 4
    assert payload["verdicts"][0]["missing_reasoning"] == 2
    assert payload["verdicts"][0]["reasoning_coverage_pct"] == 50.0
    assert payload["verdicts"][0]["confidence_bands"] == {
        "null": 1,
        "low": 1,
        "mid": 1,
        "high": 1,
    }

    print("PASS")