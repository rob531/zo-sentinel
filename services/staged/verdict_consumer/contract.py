"""verdict_consumer contract -- consumes axis scores and writes risk verdicts to mcp_risk_register."""

import json
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class VerdictResult(BaseModel):
    server_id: str
    verdict: str
    risk_level: str
    score_summary: dict[str, Any]
    computed_at: str


class RiskRegisterEntry(BaseModel):
    server_id: str
    risk_level: str
    verdict: str
    score_summary: dict[str, Any]
    computed_at: str
    decision_rule_version: str


def compute_verdict(scores: list[McpLlmAxisScore]) -> tuple[str, str, dict[str, Any]]:
    """Derive overall risk verdict from axis scores."""
    if not scores:
        return "UNKNOWN", "none", {}

    critical_count = sum(1 for s in scores if s.p_critical and s.p_critical > 0.7)
    danger_count = sum(1 for s in scores if s.p_danger and s.p_danger > 0.5)
    total = len(scores)

    avg_critical = sum(s.p_critical or 0 for s in scores) / total
    avg_danger = sum(s.p_danger or 0 for s in scores) / total
    max_critical = max((s.p_critical or 0 for s in scores), default=0)

    if max_critical > 0.85 or critical_count > total * 0.5:
        risk_level = "critical"
        verdict = "BLOCK"
    elif max_critical > 0.6 or critical_count > total * 0.3:
        risk_level = "high"
        verdict = "REVIEW"
    elif avg_danger > 0.4 or danger_count > total * 0.4:
        risk_level = "medium"
        verdict = "MONITOR"
    elif avg_critical > 0.2 or avg_danger > 0.2:
        risk_level = "low"
        verdict = "WATCH"
    else:
        risk_level = "minimal"
        verdict = "ALLOW"

    score_summary = {
        "total_scores": total,
        "critical_count": critical_count,
        "danger_count": danger_count,
        "avg_p_critical": round(avg_critical, 4),
        "avg_p_danger": round(avg_danger, 4),
        "max_p_critical": round(max_critical, 4),
    }
    return verdict, risk_level, score_summary


async def consume_scores(
    session: Annotated[Session, Depends(get_session)],
    server_id: str | None = None,
    limit: int = 100,
) -> list[VerdictResult]:
    """Consume axis scores and return verdict results."""
    query = select(McpLlmAxisScore).order_by(McpLlmAxisScore.scored_at.desc())
    if server_id:
        query = query.where(McpLlmAxisScore.server_id == server_id)
    query = query.limit(limit)

    result = session.execute(query)
    scores = list(result.scalars().all())

    if not scores:
        return []

    server_ids = list({s.server_id for s in scores if s.server_id})
    verdict_results = []

    for sid in server_ids:
        server_scores = [s for s in scores if s.server_id == sid]
        verdict, risk_level, score_summary = compute_verdict(server_scores)
        result_entry = VerdictResult(
            server_id=sid,
            verdict=verdict,
            risk_level=risk_level,
            score_summary=score_summary,
            computed_at=datetime.now(timezone.utc).isoformat(),
        )
        verdict_results.append(result_entry)

    return verdict_results


async def write_verdict_to_register(
    session: Annotated[Session, Depends(get_session)],
    entry: RiskRegisterEntry,
) -> dict[str, Any]:
    """Write a verdict entry to the mcp_risk_register bus table."""
    payload = {
        "server_id": entry.server_id,
        "risk_level": entry.risk_level,
        "verdict": entry.verdict,
        "score_summary": json.dumps(entry.score_summary),
        "computed_at": entry.computed_at,
        "decision_rule_version": entry.decision_rule_version,
    }

    insert_stmt = text("""
        INSERT INTO mcp_risk_register 
        (server_id, risk_level, verdict, score_summary, computed_at, decision_rule_version)
        VALUES 
        (:server_id, :risk_level, :verdict, :score_summary, :computed_at, :decision_rule_version)
    """)
    session.execute(insert_stmt, payload)
    session.commit()

    return {"status": "written", "server_id": entry.server_id}


def create_app() -> FastAPI:
    """Create FastAPI app for verdict_consumer."""
    app = FastAPI(title="verdict_consumer", openapi_url="/openapi.json")

    @app.post("/consume", response_model=list[VerdictResult])
    async def consume_endpoint(
        server_id: str | None = None,
        limit: int = 100,
        session: Session = Depends(get_session),
    ):
        return await consume_scores(session, server_id, limit)

    @app.post("/write", response_model=dict[str, Any])
    async def write_endpoint(
        entry: RiskRegisterEntry,
        session: Session = Depends(get_session),
    ):
        return await write_verdict_to_register(session, entry)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Determine correct import path
    project_root = Path(__file__).resolve().parents[4]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base

    # Create in-memory test DB
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Run self-test
    response = client.get("/health")
    if response.status_code != 200:
        print("FAIL: health check failed")
        sys.exit(1)

    response = client.post("/consume", params={"limit": 10})
    if response.status_code != 200:
        print("FAIL: consume endpoint failed")
        sys.exit(1)

    print("PASS")
    sys.exit(0)