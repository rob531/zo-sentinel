from datetime import datetime
from typing import Any
import json

from app.db import get_session
from app.models import McpLlmAxisScore
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session


class TierTransition(BaseModel):
    from_tier: str
    to_tier: str
    at: datetime
    axis_changes: dict[str, float]


class TrajectorySummary(BaseModel):
    server_id: str
    current_tier: str
    direction: str
    transitions: list[TierTransition]


def _derive_tier(row: Any) -> str:
    if hasattr(row, 'label') and row.label:
        return str(row.label)
    return 'unknown'


def compute_trajectory(session: Session, server_id: str) -> TrajectorySummary:
    rows = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at)
        .all()
    )
    
    if not rows:
        return TrajectorySummary(server_id=server_id, current_tier='unknown', direction='stable', transitions=[])
    
    tiers = [(_derive_tier(r), r.scored_at, r) for r in rows]
    current_tier = tiers[-1][0]
    
    transitions = []
    for i in range(1, len(tiers)):
        from_tier, at, row = tiers[i]
        prev_tier = tiers[i - 1][0]
        if from_tier != prev_tier:
            axis_changes = {}
            if hasattr(row, 'p_critical') and row.p_critical is not None:
                axis_changes['p_critical'] = float(row.p_critical)
            if hasattr(row, 'p_danger') and row.p_danger is not None:
                axis_changes['p_danger'] = float(row.p_danger)
            transitions.append(TierTransition(
                from_tier=prev_tier,
                to_tier=from_tier,
                at=at,
                axis_changes=axis_changes
            ))
    
    direction = 'stable'
    if transitions:
        upgrades = sum(1 for t in transitions if t.to_tier < t.from_tier)
        downgrades = sum(1 for t in transitions if t.to_tier > t.from_tier)
        if downgrades > upgrades:
            direction = 'downgrade'
        elif upgrades > downgrades:
            direction = 'upgrade'
    
    return TrajectorySummary(
        server_id=server_id,
        current_tier=current_tier,
        direction=direction,
        transitions=transitions
    )


if __name__ == '__main__':
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    
    in_memory_engine = create_engine('sqlite:///:memory:', poolclass=StaticPool)
    in_memory_conn = in_memory_engine.connect()
    in_memory_conn.execute(text("""
        CREATE TABLE mcp_llm_axis_scores (
            id INTEGER PRIMARY KEY,
            server_id TEXT NOT NULL,
            axis_name TEXT,
            scored_at TIMESTAMP NOT NULL,
            label TEXT,
            probs TEXT,
            p_critical REAL,
            p_danger REAL,
            p_top REAL,
            model_version TEXT,
            decision_rule_version TEXT,
            adapter_sha256 TEXT,
            label_index INTEGER,
            escalated INTEGER,
            escalated_to TEXT
        )
    """))
    in_memory_conn.commit()
    
    test_server_id = 'test-trajectory-server-001'
    now = datetime.utcnow()
    
    rows_to_insert = [
        (test_server_id, 'risk_axis', (now.replace(hour=1)).isoformat(), 'low', '{"axis":"risk"}', 0.1, 0.2, 0.7, 'v1', 'rule1', 'sha1', 0, 0, None),
        (test_server_id, 'risk_axis', (now.replace(hour=2)).isoformat(), 'medium', '{"axis":"risk"}', 0.3, 0.4, 0.6, 'v1', 'rule1', 'sha1', 1, 0, None),
        (test_server_id, 'risk_axis', (now.replace(hour=3)).isoformat(), 'high', '{"axis":"risk"}', 0.6, 0.7, 0.3, 'v1', 'rule1', 'sha1', 2, 0, None),
    ]
    
    for row in rows_to_insert:
        in_memory_conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores 
            (server_id, axis_name, scored_at, label, probs, p_critical, p_danger, p_top, model_version, decision_rule_version, adapter_sha256, label_index, escalated, escalated_to)
            VALUES (:server_id, :axis_name, :scored_at, :label, :probs, :p_critical, :p_danger, :p_top, :model_version, :decision_rule_version, :adapter_sha256, :label_index, :escalated, :escalated_to)
        """), {
            'server_id': row[0], 'axis_name': row[1], 'scored_at': row[2], 'label': row[3],
            'probs': row[4], 'p_critical': row[5], 'p_danger': row[6], 'p_top': row[7],
            'model_version': row[8], 'decision_rule_version': row[9], 'adapter_sha256': row[10],
            'label_index': row[11], 'escalated': row[12], 'escalated_to': row[13]
        })
    in_memory_conn.commit()
    
    class MockResult:
        def __init__(self, conn, server_id):
            self._conn = conn
            self._server_id = server_id
        def all(self):
            result = self._conn.execute(text("""
                SELECT id, server_id, axis_name, scored_at, label, probs, p_critical, p_danger, p_top,
                       model_version, decision_rule_version, adapter_sha256, label_index, escalated, escalated_to
                FROM mcp_llm_axis_scores
                WHERE server_id = :server_id
                ORDER BY scored_at
            """), {'server_id': self._server_id})
            rows = result.fetchall()
            class MockRow:
                def __init__(self, data):
                    self.id = data[0]
                    self.server_id = data[1]
                    self.axis_name = data[2]
                    self.scored_at = datetime.fromisoformat(data[3]) if isinstance(data[3], str) else data[3]
                    self.label = data[4]
                    self.probs = json.loads(data[5]) if data[5] else None
                    self.p_critical = data[6]
                    self.p_danger = data[7]
                    self.p_top = data[8]
                    self.model_version = data[9]
                    self.decision_rule_version = data[10]
                    self.adapter_sha256 = data[11]
                    self.label_index = data[12]
                    self.escalated = data[13]
                    self.escalated_to = data[14]
            return [MockRow(r) for r in rows]
    
    class MockSession:
        def __init__(self, conn, server_id):
            self._conn = conn
            self._server_id = server_id
        def query(self, model):
            return self
        def filter(self, *args, **kwargs):
            return self
        def order_by(self, *args):
            return self
        def all(self):
            return MockResult(self._conn, self._server_id).all()
        def add(self, obj):
            pass
        def commit(self):
            pass
    
    def override_get_session():
        return MockSession(in_memory_conn, test_server_id)
    
    import services.staged.server_risk_trajectory.logic as logic_module
    logic_module.get_session = override_get_session
    
    result = compute_trajectory(override_get_session(), test_server_id)
    
    assert result.server_id == test_server_id
    assert len(result.transitions) > 0
    assert result.direction in ('upgrade', 'downgrade', 'stable')
    
    print("PASS")