"""Logic for verdict reasoning quality audits over the real application registry."""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


def get_verdict_reasoning_quality(db: Session) -> Dict[str, Any]:
    """Compute verdict-grouped reasoning quality metrics."""
    reasoning = McpServerRegistry.verdict_reasoning
    trimmed_reasoning = func.trim(reasoning)
    has_reasoning = and_reasoning = reasoning.is_not(None) & (trimmed_reasoning != "")
    missing_reasoning = reasoning.is_(None) | (trimmed_reasoning == "")

    rows = db.execute(
        select(
            McpServerRegistry.verdict,
            func.count().label("servers"),
            func.sum(case((missing_reasoning, 1), else_=0)).label("missing_reasoning"),
            func.sum(
                case(
                    (has_reasoning & (func.length(trimmed_reasoning) < 40), 1),
                    else_=0,
                )
            ).label("hollow_reasoning"),
            func.avg(
                case(
                    (has_reasoning, func.length(trimmed_reasoning)),
                    else_=None,
                )
            ).label("avg_reasoning_length"),
            func.sum(
                case((McpServerRegistry.confidence.is_(None), 1), else_=0)
            ).label("missing_confidence"),
        )
        .group_by(McpServerRegistry.verdict)
    ).all()

    by_verdict: List[Dict[str, Any]] = []
    total_servers = 0

    for verdict, servers, missing, hollow, average, missing_confidence in rows:
        server_count = int(servers or 0)
        total_servers += server_count
        by_verdict.append(
            {
                "verdict": verdict or "unknown",
                "servers": server_count,
                "missing_reasoning": int(missing or 0),
                "hollow_reasoning": int(hollow or 0),
                "avg_reasoning_length": float(average) if average is not None else 0.0,
                "missing_confidence": int(missing_confidence or 0),
            }
        )

    by_verdict.sort(key=lambda item: item["verdict"])
    return {"total_servers": total_servers, "by_verdict": by_verdict}


compute_verdict_reasoning_quality = get_verdict_reasoning_quality
get_quality_metrics = get_verdict_reasoning_quality


def __main__() -> None:
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with testing_session() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    verdict="approved",
                    verdict_reasoning="A sufficiently detailed assessment of this server.",
                    confidence=0.9,
                ),
                McpServerRegistry(
                    server_id="server-2",
                    verdict="approved",
                    verdict_reasoning="",
                    confidence=0.8,
                ),
                McpServerRegistry(
                    server_id="server-3",
                    verdict="approved",
                    verdict_reasoning="short",
                    confidence=0.7,
                ),
                McpServerRegistry(
                    server_id="server-4",
                    verdict="approved",
                    verdict_reasoning="Another sufficiently detailed assessment.",
                    confidence=None,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/quality/verdict-reasoning")
    def verdict_reasoning_quality(db: Session = Depends(get_session)):
        return get_verdict_reasoning_quality(db)

    response = TestClient(test_app).get("/api/quality/verdict-reasoning")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_servers"] == 4

    approved = next(
        bucket for bucket in payload["by_verdict"] if bucket["verdict"] == "approved"
    )
    assert approved["missing_reasoning"] == 1
    assert approved["hollow_reasoning"] == 1
    assert approved["missing_confidence"] == 1
    print("PASS")


if __name__ == "__main__":
    __main__()