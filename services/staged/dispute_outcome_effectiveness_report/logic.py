"""Data and computation layer for the dispute outcome effectiveness report."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import and_, desc, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry


def _turnaround_hours(created_at: Optional[datetime], resolved_at: Optional[datetime]) -> Optional[float]:
    if created_at is None or resolved_at is None:
        return None

    if created_at.tzinfo is not None and resolved_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=None)
    elif resolved_at.tzinfo is not None and created_at.tzinfo is None:
        resolved_at = resolved_at.replace(tzinfo=None)
    elif created_at.tzinfo is not None and resolved_at.tzinfo is not None:
        created_at = created_at.replace(tzinfo=None)
        resolved_at = resolved_at.replace(tzinfo=None)

    seconds = (resolved_at - created_at).total_seconds()
    return max(0.0, seconds / 3600.0)


def _normalise_risk(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def get_outcome_effectiveness(db: Session) -> Dict[str, object]:
    """Compute outcome effectiveness statistics from the authoritative app tables."""
    rows = db.execute(
        select(
            McpScoreDispute.id,
            McpScoreDispute.status,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.reason_category,
            McpLlmAxisScore.p_top,
            McpServerRegistry.risk_tier,
        )
        .select_from(McpScoreDispute)
        .outerjoin(
            McpLlmAxisScore,
            and_(
                McpLlmAxisScore.server_id == McpScoreDispute.server_id,
                McpLlmAxisScore.axis_name == "overall_risk",
            ),
        )
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .order_by(McpScoreDispute.id, desc(McpLlmAxisScore.scored_at))
    ).all()

    disputes = []
    seen_dispute_ids = set()
    for row in rows:
        if row.id in seen_dispute_ids:
            continue
        seen_dispute_ids.add(row.id)
        disputes.append(row)

    by_status: Dict[str, int] = {}
    reason_counts: Dict[str, int] = {}
    turnaround_values: List[float] = []
    comparable = 0
    agreements = 0

    for dispute in disputes:
        status = str(dispute.status or "unknown")
        by_status[status] = by_status.get(status, 0) + 1

        category = str(dispute.reason_category or "unknown")
        reason_counts[category] = reason_counts.get(category, 0) + 1

        if status.lower() == "resolved":
            hours = _turnaround_hours(dispute.created_at, dispute.resolved_at)
            if hours is not None:
                turnaround_values.append(hours)

        proposed = _normalise_risk(dispute.proposed_overall_risk)
        registry_tier = _normalise_risk(dispute.risk_tier)
        if proposed is not None and registry_tier is not None:
            comparable += 1
            if proposed == registry_tier:
                agreements += 1

    mean_turnaround = (
        sum(turnaround_values) / len(turnaround_values)
        if turnaround_values
        else 0.0
    )
    agreement_rate = agreements / comparable if comparable else 0.0

    top_reason_categories = [
        {"reason_category": category, "count": count}
        for category, count in sorted(
            reason_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:5]
    ]

    return {
        "total": len(disputes),
        "by_status": by_status,
        "mean_turnaround_hours": float(mean_turnaround),
        "agreement_rate": float(agreement_rate),
        "top_reason_categories": top_reason_categories,
    }


def compute_outcome_effectiveness(db: Session) -> Dict[str, object]:
    """Compatibility alias for the report computation."""
    return get_outcome_effectiveness(db)


def get_outcome_effectiveness_report(db: Session) -> Dict[str, object]:
    """Compatibility alias for the report computation."""
    return get_outcome_effectiveness(db)


def get_outcomes_effectiveness(db: Session) -> Dict[str, object]:
    """Compatibility alias for the report computation."""
    return get_outcome_effectiveness(db)


def _run_self_test() -> None:
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    application = FastAPI()
    application.dependency_overrides[get_session] = override_get_session

    @application.get("/api/disputes/outcome-effectiveness")
    def outcome_effectiveness_endpoint(db: Session = Depends(get_session)):
        return get_outcome_effectiveness(db)

    now = datetime.utcnow()
    session = TestingSessionLocal()
    try:
        for index in range(1, 4):
            server_id = f"self-test-server-{index}"
            status = "resolved" if index < 3 else "open"
            created_at = now - timedelta(hours=index)
            resolved_at = created_at + timedelta(hours=index) if status == "resolved" else None

            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    verdict="malicious",
                    risk_tier="high",
                )
            )
            session.add(
                McpLlmAxisScore(
                    id=index,
                    server_id=server_id,
                    axis_name="overall_risk",
                    model_version="self-test",
                    p_top=0.9,
                    scored_at=now,
                )
            )
            session.add(
                McpScoreDispute(
                    id=index,
                    server_id=server_id,
                    submitted_by="self-test-user",
                    proposed_overall_risk="HIGH",
                    reason_category=f"category-{index}",
                    status=status,
                    created_at=created_at,
                    resolved_at=resolved_at,
                    explanation="self-test dispute",
                )
            )
        session.commit()
    finally:
        session.close()

    with TestClient(application) as client:
        response = client.get("/api/disputes/outcome-effectiveness")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert payload["by_status"]["resolved"] == 2
        assert payload["by_status"]["open"] == 1
        assert isinstance(payload["mean_turnaround_hours"], float)
        assert payload["mean_turnaround_hours"] >= 0

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()