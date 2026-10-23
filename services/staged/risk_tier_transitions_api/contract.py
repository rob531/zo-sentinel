from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from collections import defaultdict

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore


app = FastAPI()


class TransitionRow(BaseModel):
    date: str
    up: int
    down: int
    stable: int
    new_servers: int
    total_servers: int


class TransitionsResponse(BaseModel):
    days: int
    transitions: List[TransitionRow]


def get_risk_tier_transitions(days: int = 30, session: Session = Depends(get_session)) -> TransitionsResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)

    result = session.execute(
        text("""
            WITH scored AS (
                SELECT
                    s.server_id,
                    s.scored_at,
                    DATE(s.scored_at) as score_date,
                    s.axis_name,
                    s.label,
                    s.label_index,
                    r.risk_tier,
                    ROW_NUMBER() OVER (
                        PARTITION BY s.server_id, s.axis_name, DATE(s.scored_at)
                        ORDER BY s.scored_at DESC
                    ) as rn
                FROM McpLlmAxisScore s
                JOIN McpServerRegistry r ON s.server_id = r.server_id
                WHERE s.scored_at >= :cutoff
            ),
            ranked AS (
                SELECT
                    server_id,
                    score_date,
                    axis_name,
                    label,
                    label_index,
                    risk_tier,
                    LAG(label_index) OVER (
                        PARTITION BY server_id, axis_name
                        ORDER BY score_date DESC
                    ) as prev_label_index,
                    ROW_NUMBER() OVER (
                        PARTITION BY server_id, axis_name
                        ORDER BY score_date DESC
                    ) as rn
                FROM scored
                WHERE rn = 1
            ),
            transitions AS (
                SELECT
                    server_id,
                    axis_name,
                    score_date,
                    CASE
                        WHEN prev_label_index IS NULL THEN 'new'
                        WHEN label_index > prev_label_index THEN 'up'
                        WHEN label_index < prev_label_index THEN 'down'
                        ELSE 'stable'
                    END as transition_type
                FROM ranked
            )
            SELECT
                score_date as date,
                SUM(CASE WHEN transition_type = 'up' THEN 1 ELSE 0 END) as up,
                SUM(CASE WHEN transition_type = 'down' THEN 1 ELSE 0 END) as down,
                SUM(CASE WHEN transition_type = 'stable' THEN 1 ELSE 0 END) as stable,
                SUM(CASE WHEN transition_type = 'new' THEN 1 ELSE 0 END) as new_servers,
                COUNT(DISTINCT server_id) as total_servers
            FROM transitions
            GROUP BY score_date
            ORDER BY score_date DESC
        """),
        {"cutoff": cutoff}
    )

    rows = []
    for row in result:
        rows.append(TransitionRow(
            date=str(row.date),
            up=row.up,
            down=row.down,
            stable=row.stable,
            new_servers=row.new_servers,
            total_servers=row.total_servers
        ))

    return TransitionsResponse(days=days, transitions=rows)


@app.get("/api/risk/transitions")
def risk_tier_transitions(days: int = 30, session: Session = Depends(get_session)) -> TransitionsResponse:
    return get_risk_tier_transitions(days=days, session=session)


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    SessionLocal = sessionmaker(bind=engine)

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                server_id INTEGER PRIMARY KEY,
                name TEXT,
                url TEXT,
                risk_tier TEXT,
                trust_score REAL,
                confidence REAL,
                description TEXT,
                registry_source TEXT,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                last_assessed TIMESTAMP,
                scan_count INTEGER,
                meta TEXT,
                verdict TEXT,
                verdict_reasoning TEXT
            )
        """))

        conn.execute(text("""
            CREATE TABLE McpLlmAxisScore (
                id INTEGER PRIMARY KEY,
                server_id INTEGER,
                axis_name TEXT,
                label TEXT,
                label_index INTEGER,
                probs TEXT,
                model_version TEXT,
                adapter_sha256 TEXT,
                decision_rule_version TEXT,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                escalated INTEGER,
                escalated_to TEXT,
                scored_at TIMESTAMP
            )
        """))

        today = datetime.utcnow().date()
        yesterday = today - timedelta(days=1)

        servers = [
            (1, "server-a", "low"),
            (2, "server-b", "medium"),
            (3, "server-c", "high"),
        ]
        for sid, name, tier in servers:
            conn.execute(text("""
                INSERT INTO McpServerRegistry (server_id, name, url, risk_tier)
                VALUES (:sid, :name, :url, :tier)
            """), {"sid": sid, "name": name, "url": f"http://{name}.test", "tier": tier})

        scores = [
            (1, "security", "label_1", 1, yesterday, 1),
            (2, "security", "label_1", 1, yesterday, 1),
            (3, "security", "label_3", 3, yesterday, 1),
            (1, "security", "label_2", 2, today, 2),
            (2, "security", "label_1", 1, today, 2),
            (3, "security", "label_1", 1, today, 2),
        ]
        for sid, axis, label, idx, dt, rn in scores:
            conn.execute(text("""
                INSERT INTO McpLlmAxisScore
                (server_id, axis_name, label, label_index, probs, scored_at)
                VALUES (:sid, :axis, :label, :idx, '{}', :dt)
            """), {"sid": sid, "axis": axis, "label": label, "idx": idx, "dt": dt})

        conn.commit()

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    response = client.get("/api/risk/transitions")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert "transitions" in data, "Missing transitions in response"
    assert len(data["transitions"]) == 2, f"Expected 2 days, got {len(data['transitions'])}"

    for row in data["transitions"]:
        total = row["up"] + row["down"] + row["stable"] + row["new_servers"]
        if total == row["total_servers"]:
            break
    else:
        raise AssertionError("No row had up+down+stable+new_servers == total")

    print("PASS")