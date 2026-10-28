from __future__ import annotations

from statistics import median
from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_verdict_reasoning_length_stats(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    rows = db.execute(
        select(
            func.coalesce(McpServerRegistry.verdict, "unknown").label("verdict"),
            func.coalesce(
                func.length(McpServerRegistry.verdict_reasoning), 0
            ).label("reasoning_chars"),
            McpServerRegistry.verdict_reasoning.is_(None).label("reasoning_is_null"),
            (McpServerRegistry.verdict_reasoning == "").label("reasoning_is_empty"),
            McpServerRegistry.confidence.is_(None).label("confidence_is_null"),
        ).select_from(McpServerRegistry)
    ).all()

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        verdict = str(row.verdict)
        reasoning_is_null = bool(row.reasoning_is_null)
        reasoning_is_empty = bool(row.reasoning_is_empty)
        reasoning_present = not reasoning_is_null and not reasoning_is_empty

        grouped.setdefault(verdict, []).append(
            {
                "reasoning_chars": int(row.reasoning_chars or 0),
                "missing_reasoning": not reasoning_present,
                "null_confidence_with_reasoning": (
                    reasoning_present and bool(row.confidence_is_null)
                ),
            }
        )

    per_verdict: List[Dict[str, Any]] = []
    for verdict in sorted(grouped):
        entries = grouped[verdict]
        lengths = [entry["reasoning_chars"] for entry in entries]

        per_verdict.append(
            {
                "verdict": verdict,
                "count": len(entries),
                "reasoning_chars_min": min(lengths) if lengths else 0,
                "reasoning_chars_median": median(lengths) if lengths else 0,
                "reasoning_chars_avg": (
                    sum(lengths) / len(lengths) if lengths else 0.0
                ),
                "reasoning_chars_max": max(lengths) if lengths else 0,
                "missing_reasoning_count": sum(
                    1 for entry in entries if entry["missing_reasoning"]
                ),
                "null_confidence_count": sum(
                    1
                    for entry in entries
                    if entry["null_confidence_with_reasoning"]
                ),
            }
        )

    return {
        "per_verdict": per_verdict,
        "totals": {
            "servers": len(rows),
            "verdicts": len(per_verdict),
        },
    }


get_reasoning_length_stats = get_verdict_reasoning_length_stats
get_verdict_reasoning_length = get_verdict_reasoning_length_stats
get_verdict_reasoning_length_data = get_verdict_reasoning_length_stats
get_reasoning_length_statistics = get_verdict_reasoning_length_stats
fetch_verdict_reasoning_length_stats = get_verdict_reasoning_length_stats
compute_verdict_reasoning_length = get_verdict_reasoning_length_stats


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="reasoning-test-1",
                    name="reasoning-test-1",
                    verdict="approved",
                    verdict_reasoning="short",
                    confidence=90,
                ),
                McpServerRegistry(
                    server_id="reasoning-test-2",
                    name="reasoning-test-2",
                    verdict="approved",
                    verdict_reasoning="a considerably longer explanation",
                    confidence=None,
                ),
                McpServerRegistry(
                    server_id="reasoning-test-3",
                    name="reasoning-test-3",
                    verdict="flagged",
                    verdict_reasoning=None,
                    confidence=20,
                ),
                McpServerRegistry(
                    server_id="reasoning-test-4",
                    name="reasoning-test-4",
                    verdict="flagged",
                    verdict_reasoning="",
                    confidence=10,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/verdict-reasoning-length")
    def verdict_reasoning_length_endpoint(
        db: Session = Depends(get_session),
    ):
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **get_verdict_reasoning_length_stats(db),
        }

    response = TestClient(test_app).get(
        "/api/registry/verdict-reasoning-length"
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["totals"]["servers"] == 4
    assert payload["totals"]["verdicts"] == 2
    assert any(
        item["missing_reasoning_count"] >= 1 for item in payload["per_verdict"]
    )

    numeric_fields = (
        "count",
        "reasoning_chars_min",
        "reasoning_chars_median",
        "reasoning_chars_avg",
        "reasoning_chars_max",
        "missing_reasoning_count",
        "null_confidence_count",
    )
    for item in payload["per_verdict"]:
        for field in numeric_fields:
            assert item[field] >= 0

    print("PASS")