from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpServerRegistry


def get_verdict_reasoning_gap(session: Session) -> Dict[str, Any]:
    """
    Compute statistics about human‑readable verdict reasoning in the
    ``mcp_server_registry`` table.

    Returns a dictionary (without the ``generated_at`` timestamp; the router
    adds that).
    """
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.verdict_reasoning,
            McpServerRegistry.last_assessed,
        )
    ).all()

    total = len(rows)
    with_reasoning = 0
    total_reasoning_len = 0
    per_verdict: Dict[str, Dict[str, int]] = {}
    stale_without_reasoning = 0
    now = datetime.now(timezone.utc)

    for row in rows:
        server_id, verdict, reasoning, last_assessed = row

        has_reasoning = False
        if reasoning is not None and len(reasoning.strip()) > 0:
            has_reasoning = True
            with_reasoning += 1
            total_reasoning_len += len(reasoning)

        # per‑verdict aggregation
        if verdict not in per_verdict:
            per_verdict[verdict] = {"total": 0, "with_reasoning": 0}
        per_verdict[verdict]["total"] += 1
        if has_reasoning:
            per_verdict[verdict]["with_reasoning"] += 1

        # stale without reasoning
        if not has_reasoning and last_assessed is not None:
            if last_assessed.tzinfo is None:
                last_assessed = last_assessed.replace(tzinfo=timezone.utc)
            if now - last_assessed > timedelta(days=30):
                stale_without_reasoning += 1

    without_reasoning = total - with_reasoning
    avg_len = (total_reasoning_len / with_reasoning) if with_reasoning else 0.0
    coverage_pct = (with_reasoning / total * 100) if total else 0.0

    per_verdict_list: List[Dict[str, Any]] = []
    for v, data in per_verdict.items():
        cov = (data["with_reasoning"] / data["total"] * 100) if data["total"] else 0.0
        per_verdict_list.append(
            {
                "verdict": v,
                "total": data["total"],
                "with_reasoning": data["with_reasoning"],
                "coverage_pct": round(cov, 2),
            }
        )

    return {
        "total_servers": total,
        "with_reasoning": with_reasoning,
        "without_reasoning": without_reasoning,
        "avg_reasoning_length": round(avg_len, 2),
        "reasoning_coverage_pct": round(coverage_pct, 2),
        "per_verdict": per_verdict_list,
        "stale_without_reasoning": stale_without_reasoning,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite database (mirrors the real schema)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    # Seed data as described in the acceptance criteria
    now = datetime.now(timezone.utc)
    past = now - timedelta(days=90)

    test_rows = [
        McpServerRegistry(
            server_id="s1",
            verdict="TRUSTED_GENERAL",
            verdict_reasoning="x" * 120,
            last_assessed=now,
        ),
        McpServerRegistry(
            server_id="s2",
            verdict="TRUSTED_GENERAL",
            verdict_reasoning="",
            last_assessed=now,
        ),
        McpServerRegistry(
            server_id="s3",
            verdict="HIGH_RISK_ISOLATED",
            verdict_reasoning="some reasoning",
            last_assessed=now,
        ),
        McpServerRegistry(
            server_id="s4",
            verdict="LOW_RISK",
            verdict_reasoning=None,
            last_assessed=past,
        ),
    ]

    session.add_all(test_rows)
    session.commit()

    result = get_verdict_reasoning_gap(session)

    assert result["total_servers"] == 4
    assert result["with_reasoning"] == 2
    assert result["without_reasoning"] == 2
    assert result["reasoning_coverage_pct"] == 50.0
    assert result["stale_without_reasoning"] == 1

    print("PASS")