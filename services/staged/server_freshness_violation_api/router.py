# services/staged/server_freshness_violation_api/logic.py
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models import McpServerRegistry, McpLlmAxisScore


def get_violation_type(
    server_id: str,
    session: Session
) -> tuple[Optional[str], float, float]:
    """
    Determine freshness violation type for a server.
    Returns (violation_type, age_hours, threshold_hours) or (None, 0, 0) if compliant.
    
    Violation types:
    - 'overdue_first_verdict': no verdict within 24h of first_seen
    - 'overdue_re_verdict': not re-verdicted within 7 days of last_assessed
    - None: compliant
    """
    stmt = select(
        McpServerRegistry.first_seen,
        McpServerRegistry.last_assessed,
        McpServerRegistry.verdict,
        func.max(McpLlmAxisScore.scored_at).label('last_score_at')
    ).outerjoin(
        McpLlmAxisScore,
        McpServerRegistry.server_id == McpLlmAxisScore.server_id
    ).where(
        McpServerRegistry.server_id == server_id
    ).group_by(
        McpServerRegistry.server_id,
        McpServerRegistry.first_seen,
        McpServerRegistry.last_assessed,
        McpServerRegistry.verdict
    )
    
    result = session.execute(stmt).first()
    if not result:
        return None, 0.0, 0.0
    
    first_seen = result.first_seen
    last_assessed = result.last_assessed
    last_score_at = result.last_score_at
    
    now = datetime.now(timezone.utc)
    
    if first_seen and not last_score_at:
        age_hours = (now - first_seen).total_seconds() / 3600
        threshold_hours = 24.0
        if age_hours > threshold_hours:
            return 'overdue_first_verdict', age_hours, threshold_hours
    
    if last_assessed and last_score_at:
        age_hours = (now - last_score_at).total_seconds() / 3600
        threshold_hours = 168.0  # 7 days
        if age_hours > threshold_hours:
            return 'overdue_re_verdict', age_hours, threshold_hours
    
    return None, 0.0, 0.0


def get_freshness_violations(session: Session) -> list[dict]:
    """
    Identify servers violating PRODUCT_SPEC §4 freshness SLAs:
    (a) first verdict within 24h of first_seen
    (b) re-verdicted within 7 days of last_assessed
    """
    stmt = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.first_seen,
        McpServerRegistry.last_assessed
    ).outerjoin(
        McpLlmAxisScore,
        McpServerRegistry.server_id == McpLlmAxisScore.server_id
    ).group_by(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.first_seen,
        McpServerRegistry.last_assessed
    )
    
    results = session.execute(stmt).all()
    violations = []
    
    for row in results:
        violation_type, age_hours, threshold_hours = get_violation_type(row.server_id, session)
        if violation_type:
            violations.append({
                'server_id': row.server_id,
                'name': row.name,
                'violation_type': violation_type,
                'age_hours': round(age_hours, 2),
                'threshold_hours': threshold_hours
            })
    
    return violations