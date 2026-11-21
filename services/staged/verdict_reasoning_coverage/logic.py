"""logic.py -- reasoning-coverage data/computation layer for the verdict API."""

from __future__ import annotations

from typing import Dict

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def get_reasoning_coverage(db: Session) -> Dict[str, object]:
    """Measure reasoning coverage for servers with an overall-risk score."""
    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.verdict_reasoning,
            McpLlmAxisScore.server_id.label("scored_server_id"),
        )
        .outerjoin(
            McpLlmAxisScore,
            and_(
                McpLlmAxisScore.server_id == McpServerRegistry.server_id,
                McpLlmAxisScore.axis_name == "overall_risk",
            ),
        )
    ).all()

    servers: Dict[str, Dict[str, object]] = {}
    scored_servers = set()

    for row in rows:
        server_id = row.server_id
        if server_id not in servers:
            servers[server_id] = {
                "verdict": row.verdict or "unknown",
                "reasoning": row.verdict_reasoning,
                "scored": False,
            }
        if row.scored_server_id is not None:
            scored_servers.add(server_id)
            servers[server_id]["scored"] = True

    total_servers = len(servers)
    scored_count = len(scored_servers)
    unscored_count = total_servers - scored_count

    by_verdict: Dict[str, Dict[str, int]] = {}
    with_reasoning = 0
    empty_reasoning_examples = []

    for server_id, server in servers.items():
        verdict = str(server["verdict"])
        reasoning = server["reasoning"]
        has_reasoning = isinstance(reasoning, str) and bool(reasoning.strip())
        scored = bool(server["scored"])

        if verdict not in by_verdict:
            by_verdict[verdict] = {"count": 0, "with_reasoning": 0}
        by_verdict[verdict]["count"] += 1

        if scored and has_reasoning:
            with_reasoning += 1
            by_verdict[verdict]["with_reasoning"] += 1
        elif scored:
            empty_reasoning_examples.append(server_id)

    reasoning_coverage_pct = (
        round((with_reasoning / scored_count) * 100, 2)
        if scored_count
        else 0.0
    )

    return {
        "total_servers": total_servers,
        "scored_servers": scored_count,
        "unscored_servers": unscored_count,
        "with_reasoning": with_reasoning,
        "reasoning_coverage_pct": reasoning_coverage_pct,
        "by_verdict": by_verdict,
        "empty_reasoning_examples": sorted(empty_reasoning_examples),
    }


def reasoning_coverage(db: Session) -> Dict[str, object]:
    """Compatibility alias for callers using the service name as the function name."""
    return get_reasoning_coverage(db)


def calculate_reasoning_coverage(db: Session) -> Dict[str, object]:
    """Compatibility alias for callers using an explicit calculation name."""
    return get_reasoning_coverage(db)


def _self_test() -> None:
    from fastapi import Depends, FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    test_app = FastAPI()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/verdict/reasoning-coverage")
    def endpoint(db: Session = Depends(get_session)):
        return get_reasoning_coverage(db)

    db = TestingSessionLocal()
    try:
        verdicts = ["safe", "review", "block"]
        for index, verdict in enumerate(verdicts, start=1):
            db.add(
                McpServerRegistry(
                    server_id=f"scored-{index}",
                    verdict=verdict,
                    verdict_reasoning=(
                        "A human-readable justification was supplied."
                        if index < 3
                        else ""
                    ),
                    confidence=0.8,
                    risk_tier="medium",
                )
            )
            db.add(
                McpLlmAxisScore(
                    id=index,
                    server_id=f"scored-{index}",
                    axis_name="overall_risk",
                    model_version="test-model",
                )
            )

        for index, verdict in enumerate(verdicts, start=4):
            db.add(
                McpServerRegistry(
                    server_id=f"unscored-{index}",
                    verdict=verdict,
                    confidence=0.5,
                    risk_tier="low",
                )
            )
        db.commit()

        result = endpoint(db=db)
        assert result["total_servers"] == 6
        assert result["scored_servers"] == 3
        assert result["unscored_servers"] == 3
        assert result["with_reasoning"] == 2
        assert result["reasoning_coverage_pct"] == 66.67
        assert set(result["by_verdict"]) == set(verdicts)
        assert result["empty_reasoning_examples"] == ["scored-3"]
    finally:
        db.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()