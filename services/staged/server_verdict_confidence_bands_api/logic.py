from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import case, func, literal, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_BANDS = (
    "0.0-0.2",
    "0.2-0.4",
    "0.4-0.6",
    "0.6-0.8",
    "0.8-1.0",
)


def _band_expression():
    confidence = McpServerRegistry.confidence
    return case(
        (confidence < 0.2, literal("0.0-0.2")),
        (confidence < 0.4, literal("0.2-0.4")),
        (confidence < 0.6, literal("0.4-0.6")),
        (confidence < 0.8, literal("0.6-0.8")),
        else_=literal("0.8-1.0"),
    )


def get_verdict_confidence_bands(
    session: Session = Depends(get_session),
    verdict: str | None = None,
    min_confidence: float | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    conditions = []

    if verdict is not None and verdict.strip():
        conditions.append(
            func.lower(McpServerRegistry.verdict) == verdict.strip().lower()
        )

    if min_confidence is not None:
        conditions.append(McpServerRegistry.confidence >= min_confidence)

    band_expression = _band_expression()

    count_rows = session.execute(
        select(band_expression.label("band"), func.count().label("count"))
        .where(*conditions)
        .group_by(band_expression)
    ).all()

    counts = {band: 0 for band in _BANDS}
    for row in count_rows:
        counts[row.band] = int(row.count)

    verdict_rows = session.execute(
        select(McpServerRegistry.verdict, func.count().label("count"))
        .where(*conditions)
        .group_by(McpServerRegistry.verdict)
    ).all()

    by_verdict: dict[str, int] = {}
    for row in verdict_rows:
        key = row.verdict if row.verdict is not None else "UNKNOWN"
        by_verdict[str(key)] = int(row.count)

    band_server_rows = session.execute(
        select(band_expression.label("band"), McpServerRegistry.server_id)
        .where(*conditions)
        .order_by(McpServerRegistry.server_id)
    ).all()

    server_ids = {band: [] for band in _BANDS}
    for row in band_server_rows:
        if row.server_id is not None:
            server_ids[row.band].append(row.server_id)

    bounded_limit = max(0, int(limit))
    low_confidence_rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.confidence,
            McpServerRegistry.last_assessed,
        )
        .where(*conditions)
        .order_by(
            McpServerRegistry.confidence.asc(),
            McpServerRegistry.server_id.asc(),
        )
        .limit(bounded_limit)
    ).all()

    low_confidence = [
        {
            "server_id": row.server_id,
            "verdict": row.verdict,
            "risk_tier": row.risk_tier,
            "confidence": row.confidence,
            "last_assessed": row.last_assessed,
        }
        for row in low_confidence_rows
    ]

    bands = [
        {
            "band": band,
            "count": counts[band],
            "server_ids": server_ids[band],
        }
        for band in _BANDS
    ]

    return {
        "bands": bands,
        "total": sum(counts.values()),
        "by_verdict": by_verdict,
        "low_confidence": low_confidence,
    }


compute_verdict_confidence_bands = get_verdict_confidence_bands


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/analytics/verdict-confidence-bands")
    def verdict_confidence_bands_route(
        verdict: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 200,
        session: Session = Depends(get_session),
    ):
        return get_verdict_confidence_bands(
            session=session,
            verdict=verdict,
            min_confidence=min_confidence,
            limit=limit,
        )

    with TestSession() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="server-low",
                    verdict="TRUSTED_GENERAL",
                    confidence=0.1,
                    risk_tier="LOW",
                    last_assessed=datetime(2025, 1, 1, tzinfo=timezone.utc),
                ),
                McpServerRegistry(
                    server_id="server-medium",
                    verdict="REVIEW",
                    confidence=0.5,
                    risk_tier="MEDIUM",
                    last_assessed=datetime(2025, 1, 2, tzinfo=timezone.utc),
                ),
                McpServerRegistry(
                    server_id="server-high",
                    verdict="UNTRUSTED",
                    confidence=0.9,
                    risk_tier="HIGH",
                    last_assessed=datetime(2025, 1, 3, tzinfo=timezone.utc),
                ),
            ]
        )
        db.commit()

    client = TestClient(test_app)
    response = client.get(
        "/api/analytics/verdict-confidence-bands",
        params={"min_confidence": 0.0, "limit": 200},
    )

    assert response.status_code == 200
    payload = response.json()
    assert sum(band["count"] for band in payload["bands"]) == payload["total"]
    assert payload["total"] == 3
    assert "TRUSTED_GENERAL" in payload["by_verdict"]
    assert payload["low_confidence"][0]["server_id"] == "server-low"

    print("PASS")