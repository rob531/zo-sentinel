from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry


_TIER_ORDER = {
    "LOW": 0,
    "MEDIUM": 1,
    "HIGH": 2,
    "CRITICAL": 3,
}

_TIER_ALIASES = {
    "MED": "MEDIUM",
    "MODERATE": "MEDIUM",
    "HIGH": "HIGH",
    "CRIT": "CRITICAL",
    "LOW": "LOW",
}


def _normalize_tier(value: Any) -> str:
    if value is None:
        return "UNKNOWN"

    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        score = float(value)
        if score >= 0.8:
            return "CRITICAL"
        if score >= 0.6:
            return "HIGH"
        if score >= 0.3:
            return "MEDIUM"
        return "LOW"

    text = str(value).strip().upper()
    if text in _TIER_ORDER:
        return text
    if text in _TIER_ALIASES:
        return _TIER_ALIASES[text]

    try:
        return _normalize_tier(float(text))
    except (TypeError, ValueError):
        return "UNKNOWN"


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _days_open(created_at: Any, now: datetime) -> int:
    created = _as_datetime(created_at)
    if created is None:
        return 0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return max(0, (now - created).days)


def get_score_dispute_workflow(
    session: Session = Depends(get_session),
    status_filter: str = "open",
    limit: int = 50,
    **kwargs: Any,
) -> Dict[str, Any]:
    status = kwargs.pop("status", status_filter)
    if kwargs:
        raise TypeError(f"Unexpected keyword arguments: {', '.join(kwargs)}")

    normalized_status = str(status or "open").strip().lower()
    max_items = max(1, min(int(limit), 500))

    latest_scores = (
        select(
            McpLlmAxisScore.server_id.label("server_id"),
            McpLlmAxisScore.p_top.label("p_top"),
            McpLlmAxisScore.scored_at.label("scored_at"),
            func.row_number()
            .over(
                partition_by=McpLlmAxisScore.server_id,
                order_by=(
                    McpLlmAxisScore.scored_at.is_(None),
                    McpLlmAxisScore.scored_at.desc(),
                    McpLlmAxisScore.id.desc(),
                ),
            )
            .label("row_number"),
        )
        .where(McpLlmAxisScore.axis_name == "overall_risk")
        .subquery()
    )

    statement = (
        select(
            McpScoreDispute.id,
            McpScoreDispute.server_id,
            McpServerRegistry.name.label("server_name"),
            McpServerRegistry.risk_tier.label("current_tier"),
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.reason_category,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
            latest_scores.c.p_top,
        )
        .select_from(McpScoreDispute)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .outerjoin(
            latest_scores,
            and_(
                latest_scores.c.server_id == McpScoreDispute.server_id,
                latest_scores.c.row_number == 1,
            ),
        )
    )

    if normalized_status not in {"", "all", "any"}:
        statement = statement.where(McpScoreDispute.status == normalized_status)

    rows = session.execute(statement).mappings().all()
    now = datetime.now(timezone.utc)
    disputes: List[Dict[str, Any]] = []

    for row in rows:
        current_tier = _normalize_tier(row["current_tier"])
        if current_tier == "UNKNOWN" and row["p_top"] is not None:
            current_tier = _normalize_tier(row["p_top"])

        proposed_tier = _normalize_tier(row["proposed_overall_risk"])
        current_rank = _TIER_ORDER.get(current_tier)
        proposed_rank = _TIER_ORDER.get(proposed_tier)

        if current_rank is None or proposed_rank is None:
            tier_delta = 0
            direction = "unknown"
        else:
            tier_delta = abs(proposed_rank - current_rank)
            if proposed_rank > current_rank:
                direction = "escalation"
            elif proposed_rank < current_rank:
                direction = "downgrade"
            else:
                direction = "none"

        disputes.append(
            {
                "id": row["id"],
                "server_id": row["server_id"],
                "server_name": row["server_name"],
                "current_tier": current_tier,
                "proposed_tier": proposed_tier,
                "proposed_overall_risk": row["proposed_overall_risk"],
                "tier_delta": tier_delta,
                "direction": direction,
                "reason_category": row["reason_category"],
                "days_open": _days_open(row["created_at"], now),
                "created_at": row["created_at"],
                "resolved_at": row["resolved_at"],
            }
        )

    disputes.sort(key=lambda item: item["days_open"], reverse=True)
    disputes = disputes[:max_items]

    return {
        "status_filter": normalized_status,
        "count": len(disputes),
        "disputes": disputes,
    }


def get_dispute_workflow(
    session: Session = Depends(get_session),
    status: str = "open",
    limit: int = 50,
) -> Dict[str, Any]:
    return get_score_dispute_workflow(
        session=session,
        status_filter=status,
        limit=limit,
    )


def get_score_disputes_workflow(
    session: Session = Depends(get_session),
    status: str = "open",
    limit: int = 50,
) -> Dict[str, Any]:
    return get_score_dispute_workflow(
        session=session,
        status_filter=status,
        limit=limit,
    )


def get_workflow(
    session: Session = Depends(get_session),
    status: str = "open",
    limit: int = 50,
) -> Dict[str, Any]:
    return get_score_dispute_workflow(
        session=session,
        status_filter=status,
        limit=limit,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite+pysqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)

    McpServerRegistry.__table__.create(test_engine)
    McpLlmAxisScore.__table__.create(test_engine)
    McpScoreDispute.__table__.create(test_engine)

    test_app = FastAPI()

    def override_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    @test_app.get("/api/disputes/workflow")
    def workflow_endpoint(
        status: str = "all",
        limit: int = 50,
        session: Session = Depends(get_session),
    ):
        return get_score_dispute_workflow(
            session=session,
            status_filter=status,
            limit=limit,
        )

    base_time = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-low",
                    name="Low Risk Server",
                    risk_tier="LOW",
                    trust_score=0.95,
                ),
                McpServerRegistry(
                    server_id="server-high",
                    name="High Risk Server",
                    risk_tier="HIGH",
                    trust_score=0.40,
                ),
                McpServerRegistry(
                    server_id="server-critical",
                    name="Critical Risk Server",
                    risk_tier="CRITICAL",
                    trust_score=0.05,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-low",
                    axis_name="overall_risk",
                    model_version="test-model",
                    p_top=0.1,
                    scored_at=base_time,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-high",
                    axis_name="overall_risk",
                    model_version="test-model",
                    p_top=0.7,
                    scored_at=base_time,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-critical",
                    axis_name="overall_risk",
                    model_version="test-model",
                    p_top=0.9,
                    scored_at=base_time,
                ),
            ]
        )

        dispute_specs = [
            (1, "server-low", 0.1, "open", 0),
            (2, "server-high", 0.9, "open", 1),
            (3, "server-critical", 0.1, "open", 2),
            (4, "server-low", 0.2, "open", 3),
            (5, "server-high", 0.8, "resolved", 4),
        ]
        for dispute_id, server_id, proposed_risk, status_value, age in dispute_specs:
            created_at = base_time.replace(
                tzinfo=None
            ) - __import__("datetime").timedelta(days=age)
            resolved_at = None
            if status_value == "resolved":
                resolved_at = created_at + __import__("datetime").timedelta(hours=1)
            session.add(
                McpScoreDispute(
                    id=dispute_id,
                    server_id=server_id,
                    submitted_by="test-user",
                    proposed_overall_risk=proposed_risk,
                    proposed_axes={"overall_risk": proposed_risk},
                    reason_category="test",
                    explanation="Self-test dispute",
                    status=status_value,
                    created_at=created_at,
                    resolved_at=resolved_at,
                )
            )
        session.commit()

    with TestSession() as session:
        result = get_score_dispute_workflow(
            session=session,
            status_filter="all",
            limit=50,
        )
        assert result["count"] == 5
        assert len(result["disputes"]) == 5
        assert all(isinstance(item["days_open"], int) for item in result["disputes"])
        assert all(item["days_open"] >= 0 for item in result["disputes"])
        critical = next(
            item for item in result["disputes"] if item["server_id"] == "server-critical"
        )
        assert critical["direction"] == "downgrade"
        resolved = next(
            item for item in result["disputes"] if item["status"] == "resolved"
        ) if any("status" in item for item in result["disputes"]) else next(
            item for item in result["disputes"] if item["resolved_at"] is not None
        )
        assert resolved["resolved_at"] is not None

    print("PASS")