from datetime import datetime, timedelta
from typing import Dict

from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

app = FastAPI()
router = app


class AuthStrengthResponse(BaseModel):
    days: int
    total_servers: int
    tier_distribution: Dict[str, int]


@router.get("/api/scoring/auth-strength", response_model=AuthStrengthResponse)
def get_auth_strength(days: int = 7, session: Session = Depends(get_session)):
    cutoff = datetime.utcnow() - timedelta(days=days)

    query = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.p_top).label('max_p_top')
        )
        .join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
        .where(McpLlmAxisScore.axis_name == 'auth_strength')
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(McpLlmAxisScore.server_id)
    )

    results = session.execute(query).fetchall()

    tier_distribution: Dict[str, int] = {}
    for row in results:
        p_top = row.max_p_top
        if p_top > 75:
            tier = "TRUSTED_GENERAL"
        elif p_top > 60:
            tier = "TRUSTED_RESEARCH"
        elif p_top > 45:
            tier = "ENTERPRISE_CONTROLLED"
        elif p_top > 30:
            tier = "CAUTION_LIMITED"
        elif p_top > 15:
            tier = "HIGH_RISK_ISOLATED"
        else:
            tier = "KNOWN_THREAT"
        tier_distribution[tier] = tier_distribution.get(tier, 0) + 1

    return AuthStrengthResponse(
        days=days,
        total_servers=len(results),
        tier_distribution=tier_distribution
    )


if __name__ == "__main__":
    import sys
    from sqlalchemy import Table, Column, Integer, String, Float, DateTime, MetaData, ForeignKey, Boolean

    metadata = MetaData()

    test_server_registry = Table(
        'mcp_server_registry', metadata,
        Column('server_id', Integer, primary_key=True),
        Column('name', String),
        Column('url', String),
        Column('risk_tier', String),
        Column('last_seen', DateTime),
        Column('registry_source', String),
        Column('confidence', Float),
        Column('trust_score', Float),
        Column('verdict', String),
        Column('verdict_reasoning', String),
        Column('description', String),
        Column('meta', String),
        Column('first_seen', DateTime),
        Column('last_scanned', DateTime),
        Column('last_assessed', DateTime),
        Column('scan_count', Integer)
    )

    test_llm_axis_scores = Table(
        'mcp_llm_axis_scores', metadata,
        Column('id', Integer, primary_key=True),
        Column('server_id', Integer, ForeignKey('mcp_server_registry.server_id')),
        Column('axis_name', String),
        Column('p_top', Float),
        Column('p_critical', Float),
        Column('p_danger', Float),
        Column('label', String),
        Column('label_index', Integer),
        Column('probs', String),
        Column('model_version', String),
        Column('decision_rule_version', String),
        Column('adapter_sha256', String),
        Column('escalated', Boolean),
        Column('escalated_to', String),
        Column('scored_at', DateTime)
    )

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    metadata.create_all(engine)

    now = datetime.utcnow()
    yesterday = now - timedelta(days=1)

    with engine.connect() as conn:
        conn.execute(test_server_registry.insert().values([
            {'server_id': 1, 'name': 'Server A', 'url': 'http://a.com', 'risk_tier': None, 'last_seen': now, 'registry_source': 'test', 'confidence': 0.9, 'trust_score': 0.8, 'verdict': None, 'verdict_reasoning': None, 'description': None, 'meta': None, 'first_seen': now, 'last_scanned': now, 'last_assessed': now, 'scan_count': 1},
            {'server_id': 2, 'name': 'Server B', 'url': 'http://b.com', 'risk_tier': None, 'last_seen': now, 'registry_source': 'test', 'confidence': 0.7, 'trust_score': 0.6, 'verdict': None, 'verdict_reasoning': None, 'description': None, 'meta': None, 'first_seen': now, 'last_scanned': now, 'last_assessed': now, 'scan_count': 1},
            {'server_id': 3, 'name': 'Server C', 'url': 'http://c.com', 'risk_tier': None, 'last_seen': now, 'registry_source': 'test', 'confidence': 0.5, 'trust_score': 0.4, 'verdict': None, 'verdict_reasoning': None, 'description': None, 'meta': None, 'first_seen': now, 'last_scanned': now, 'last_assessed': now, 'scan_count': 1},
        ]))
        conn.execute(test_llm_axis_scores.insert().values([
            {'server_id': 1, 'axis_name': 'auth_strength', 'p_top': 0.80, 'p_critical': 0.10, 'p_danger': 0.10, 'label': 'TRUSTED', 'label_index': 0, 'probs': '[0.80, 0.10, 0.10]', 'model_version': 'v1', 'decision_rule_version': 'v1', 'adapter_sha256': 'sha256_a', 'escalated': False, 'escalated_to': None, 'scored_at': yesterday},
            {'server_id': 1, 'axis_name': 'auth_strength', 'p_top': 0.82, 'p_critical': 0.09, 'p_danger': 0.09, 'label': 'TRUSTED', 'label_index': 0, 'probs': '[0.82, 0.09, 0.09]', 'model_version': 'v1', 'decision_rule_version': 'v1', 'adapter_sha256': 'sha256_a', 'escalated': False, 'escalated_to': None, 'scored_at': now},
            {'server_id': 2, 'axis_name': 'auth_strength', 'p_top': 0.32, 'p_critical': 0.34, 'p_danger': 0.34, 'label': 'CAUTION', 'label_index': 2, 'probs': '[0.32, 0.34, 0.34]', 'model_version': 'v1', 'decision_rule_version': 'v1', 'adapter_sha256': 'sha256_b', 'escalated': False, 'escalated_to': None, 'scored_at': yesterday},
            {'server_id': 2, 'axis_name': 'auth_strength', 'p_top': 0.35, 'p_critical': 0.33, 'p_danger': 0.32, 'label': 'CAUTION', 'label_index': 2, 'probs': '[0.35, 0.33, 0.32]', 'model_version': 'v1', 'decision_rule_version': 'v1', 'adapter_sha256': 'sha256_b', 'escalated': False, 'escalated_to': None, 'scored_at': now},
            {'server_id': 3, 'axis_name': 'auth_strength', 'p_top': 0.10, 'p_critical': 0.30, 'p_danger': 0.60, 'label': 'RISKY', 'label_index': 3, 'probs': '[0.10, 0.30, 0.60]', 'model_version': 'v1', 'decision_rule_version': 'v1', 'adapter_sha256': 'sha256_c', 'escalated': False, 'escalated_to': None, 'scored_at': yesterday},
            {'server_id': 3, 'axis_name': 'auth_strength', 'p_top': 0.12, 'p_critical': 0.28, 'p_danger': 0.60, 'label': 'RISKY', 'label_index': 3, 'probs': '[0.12, 0.28, 0.60]', 'model_version': 'v1', 'decision_rule_version': 'v1', 'adapter_sha256': 'sha256_c', 'escalated': False, 'escalated_to': None, 'scored_at': now},
        ]))

    TestingSession = sessionmaker(bind=engine)

    def override_get_session():
        with TestingSession() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/scoring/auth-strength?days=2")
    assert response.status_code == 200
    data = response.json()
    assert "tier_distribution" in data
    print("PASS")
    sys.exit(0)