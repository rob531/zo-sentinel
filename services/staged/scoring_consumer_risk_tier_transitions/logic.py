from datetime import datetime, timedelta
from typing import Dict, Any

from fastapi import Depends
from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def _fetch_daily_risk_tier_counts(
    session: Session, start: datetime, end: datetime
) -> Dict[str, Dict[str, int]]:
    stmt = (
        select(
            func.date_trunc("day", McpLlmAxisScore.scored_at).label("day"),
            McpServerRegistry.risk_tier,
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("cnt"),
        )
        .join(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .where(
            and_(
                McpLlmAxisScore.scored_at >= start,
                McpLlmAxisScore.scored_at <= end,
            )
        )
        .group_by("day", McpServerRegistry.risk_tier)
        .order_by("day")
    )
    rows = session.execute(stmt).all()
    out: Dict[str, Dict[str, int]] = {}
    for row in rows:
        day_key = row.day.date().isoformat()
        tier = row.risk_tier
        cnt = row.cnt
        out.setdefault(day_key, {})[tier] = cnt
    return out


def compute_risk_tier_transition_counts(
    window_days: int = 30, session: Session = Depends(get_session)
) -> Dict[str, Dict[str, int]]:
    end = datetime.utcnow()
    start = end - timedelta(days=window_days)
    return _fetch_daily_risk_tier_counts(session, start, end)


if __name__ == "__main__":
    class _MockSession:
        def execute(self, stmt):
            class _Result:
                def all(self):
                    return []

            return _Result()

    mock_session = _MockSession()
    result = compute_risk_tier_transition_counts(window_days=30, session=mock_session)
    if isinstance(result, dict):
        print("PASS")
    else:
        print("FAIL")