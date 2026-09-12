from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from app.db import get_session
from app.models import McpLlmAxisScore


def consume_axis_disagreement(db, server_id: str) -> Optional[Dict[str, Any]]:
    """
    Consume axis scores and compute axis disagreement for a server.
    Reads axis scores from McpLlmAxisScore for the server over the last 7 days,
    computes per-day disagreement, and stores results in mcp_axis_disagreement_scores.
    """
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    
    scores = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at >= seven_days_ago
    ).all()
    
    if not scores:
        return None
    
    result = _compute_disagreement(scores, server_id)
    
    db.execute(
        __table__.insert().values(
            server_id=result['server_id'],
            date=result['date'],
            disagreement_count=result['disagreement_count'],
            high_risk_axes=result['high_risk_axes'],
            safe_axes=result['safe_axes'],
            score=result['score'],
            computed_at=result['computed_at']
        )
    )
    db.commit()
    
    return result


def get_axis_disagreement(db, server_id: str) -> Optional[Dict[str, Any]]:
    """Get the latest axis disagreement result for a server."""
    from sqlalchemy import desc
    
    row = db.execute(
        __table__.select()
        .where(__table__.c.server_id == server_id)
        .order_by(desc(__table__.c.computed_at))
        .limit(1)
    ).fetchone()
    
    if not row:
        return None
    
    return {
        'server_id': row.server_id,
        'date': row.date.isoformat() if row.date else None,
        'disagreement_count': row.disagreement_count,
        'high_risk_axes': row.high_risk_axes,
        'safe_axes': row.safe_axes,
        'score': row.score,
        'computed_at': row.computed_at.isoformat() if row.computed_at else None
    }


def _compute_disagreement(scores: List, server_id: str) -> Dict[str, Any]:
    high_risk_axes = []
    safe_axes = []
    
    for score in scores:
        if score.label in ('HIGH_RISK', 'DANGER'):
            if score.axis_name not in high_risk_axes:
                high_risk_axes.append(score.axis_name)
        elif score.label in ('TRUSTED_LOW', 'SAFE'):
            if score.axis_name not in safe_axes:
                safe_axes.append(score.axis_name)
    
    disagreement_count = len(high_risk_axes) * len(safe_axes)
    score = disagreement_count / 42.0 if disagreement_count > 0 else 0.0
    
    dates = sorted(set(s.scored_at.date() for s in scores if s.scored_at))
    date = dates[-1] if dates else datetime.utcnow().date()
    
    return {
        'server_id': server_id,
        'date': date,
        'disagreement_count': disagreement_count,
        'high_risk_axes': high_risk_axes,
        'safe_axes': safe_axes,
        'score': score,
        'computed_at': datetime.utcnow()
    }


__all__ = ['consume_axis_disagreement', 'get_axis_disagreement']


from sqlalchemy import Table, Column, String, Integer, Float, Date, DateTime, JSON, MetaData

__table__ = Table(
    'mcp_axis_disagreement_scores',
    MetaData(),
    Column('server_id', String, primary_key=True),
    Column('date', Date, primary_key=True),
    Column('disagreement_count', Integer),
    Column('high_risk_axes', JSON),
    Column('safe_axes', JSON),
    Column('score', Float),
    Column('computed_at', DateTime)
)


if __name__ == '__main__':
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    
    engine = create_engine('sqlite:///:memory:', poolclass=StaticPool, echo=False)
    metadata = MetaData()
    
    axis_table = Table(
        'McpLlmAxisScore',
        metadata,
        Column('id', Integer, primary_key=True, autoincrement=True),
        Column('server_id', String),
        Column('adapter_sha256', String),
        Column('axis_name', String),
        Column('model_version', String),
        Column('label_index', Integer),
        Column('probs', String),
        Column('p_top', Float),
        Column('p_critical', Float),
        Column('p_danger', Float),
        Column('label', String),
        Column('scored_at', DateTime),
        Column('decision_rule_version', String),
        Column('escalated', Integer),
        Column('escalated_to', String)
    )
    
    result_table = Table(
        'mcp_axis_disagreement_scores',
        metadata,
        Column('server_id', String, primary_key=True),
        Column('date', Date, primary_key=True),
        Column('disagreement_count', Integer),
        Column('high_risk_axes', JSON),
        Column('safe_axes', JSON),
        Column('score', Float),
        Column('computed_at', DateTime)
    )
    
    metadata.create_all(engine)
    
    def override_get_session():
        connection = engine.connect()
        return connection
    
    app = FastAPI()
    client = TestClient(app)
    app.dependency_overrides[get_session] = override_get_session
    
    with engine.connect() as conn:
        day1 = datetime(2025, 1, 15, 10, 0, 0)
        day2 = datetime(2025, 1, 16, 10, 0, 0)
        
        test_data = [
            ('s1', 'overall_risk', 'DANGER', day1),
            ('s1', 'capability_breadth', 'SAFE', day1),
            ('s1', 'overall_risk', 'HIGH_RISK', day2),
            ('s1', 'network_egress', 'TRUSTED_LOW', day2),
            ('s2', 'overall_risk', 'TRUSTED_LOW', day1),
            ('s2', 'capability_breadth', 'SAFE', day1),
            ('s2', 'data_sensitivity', 'SAFE', day2),
            ('s2', 'network_egress', 'SAFE', day2),
            ('s3', 'exploit_surface', 'HIGH_RISK', day1),
            ('s3', 'maintainer_trust', 'SAFE', day1),
            ('s3', 'auth_strength', 'DANGER', day2),
            ('s3', 'exploit_surface', 'SAFE', day2),
        ]
        
        for server_id, axis_name, label, scored_at in test_data:
            conn.execute(
                axis_table.insert().values(
                    server_id=server_id,
                    adapter_sha256='test_sha',
                    axis_name=axis_name,
                    model_version='v1',
                    label_index=0,
                    probs='[]',
                    p_top=0.0,
                    p_critical=0.0,
                    p_danger=0.0,
                    label=label,
                    scored_at=scored_at,
                    decision_rule_version='v1',
                    escalated=0,
                    escalated_to=None
                )
            )
        conn.commit()
    
    with engine.connect() as conn:
        for server_id in ['s1', 's2', 's3']:
            scores = conn.execute(
                axis_table.select().where(axis_table.c.server_id == server_id)
            ).fetchall()
            
            scores_objs = []
            for row in scores:
                class ScoreObj:
                    pass
                obj = ScoreObj()
                obj.server_id = row.server_id
                obj.axis_name = row.axis_name
                obj.label = row.label
                obj.scored_at = row.scored_at
                scores_objs.append(obj)
            
            result = _compute_disagreement(scores_objs, server_id)
            conn.execute(
                result_table.insert().values(
                    server_id=result['server_id'],
                    date=result['date'],
                    disagreement_count=result['disagreement_count'],
                    high_risk_axes=result['high_risk_axes'],
                    safe_axes=result['safe_axes'],
                    score=result['score'],
                    computed_at=result['computed_at']
                )
            )
            conn.commit()
    
    found_disagreement = False
    with engine.connect() as conn:
        for row in conn.execute(result_table.select()).fetchall():
            if row.disagreement_count >= 1:
                found_disagreement = True
                break
    
    if found_disagreement:
        print("PASS")
    else:
        print("FAIL")