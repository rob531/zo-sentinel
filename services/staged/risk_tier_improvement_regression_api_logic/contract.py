from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

app = FastAPI()


class ServerChangeEntry(BaseModel):
    server_id: int
    name: str
    old_score: float
    new_score: float
    delta: float
    risk_tier: str


class ImprovementRegressionResponse(BaseModel):
    window_days: int
    improved: List[ServerChangeEntry]
    regressed: List[ServerChangeEntry]
    unchanged: int
    total: int


@app.get("/api/risk/improvement-regression", response_model=ImprovementRegressionResponse)
def get_improvement_regression(window_days: int = 30, session: Session = Depends(get_session)):
    now = datetime.utcnow()
    window_start = now - timedelta(days=window_days)
    older_window_start = window_start - timedelta(days=window_days)

    result = session.execute(text('''
        SELECT 
            s.id as server_id,
            s.name,
            as_recent.p_top as new_score,
            as_older.p_top as old_score,
            s.risk_tier
        FROM McpServerRegistry s
        INNER JOIN McpLlmAxisScore as_recent ON s.id = as_recent.server_id
            AND as_recent.metric_axis = 'overall_risk'
            AND as_recent.recorded_at >= :window_start
        LEFT JOIN McpLlmAxisScore AS as_older ON s.id = as_older.server_id
            AND as_older.metric_axis = 'overall_risk'
            AND as_older.recorded_at < :window_start
            AND as_older.recorded_at >= :older_window_start
        WHERE 
            as_recent.server_id IS NOT NULL
    '''), {
        'window_start': window_start,
        'older_window_start': older_window_start
    })

    improved = []
    regressed = []
    unchanged = 0

    for row in result.fetchall():
        old_score = row.old_score if row.old_score is not None else 0.0
        new_score = row.new_score if row.new_score is not None else 0.0
        delta = new_score - old_score

        entry = {
            'server_id': row.server_id,
            'name': row.name,
            'old_score': old_score,
            'new_score': new_score,
            'delta': delta,
            'risk_tier': row.risk_tier
        }

        if delta > 0.1:
            improved.append(entry)
        elif delta < -0.1:
            regressed.append(entry)
        else:
            unchanged += 1

    return ImprovementRegressionResponse(
        window_days=window_days,
        improved=improved,
        regressed=regressed,
        unchanged=unchanged,
        total=len(improved) + len(regressed) + unchanged
    )


def create_test_session():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    testing_session = sessionmaker(bind=test_engine)
    test_session = testing_session()
    test_session.execute(text('''
        CREATE TABLE IF NOT EXISTS McpServerRegistry (
            id INTEGER PRIMARY KEY,
            name VARCHAR NOT NULL,
            risk_tier VARCHAR NOT NULL,
            contact_email VARCHAR,
            description VARCHAR,
            homepage VARCHAR,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
    '''))
    test_session.execute(text('''
        CREATE TABLE IF NOT EXISTS McpLlmAxisScore (
            id INTEGER PRIMARY KEY,
            server_id INTEGER NOT NULL,
            metric_axis VARCHAR NOT NULL,
            p_top FLOAT NOT NULL,
            recorded_at TIMESTAMP NOT NULL,
            FOREIGN KEY (server_id) REFERENCES McpServerRegistry(id)
        )
    '''))
    test_session.commit()
    return test_session


def seed_test_data(session: Session):
    servers = [
        (1, "server_1", "low"),
        (2, "server_2", "medium"),
        (3, "server_3", "high"),
        (4, "server_4", "critical"),
        (5, "server_5", "none"),
    ]
    for sid, name, tier in servers:
        session.execute(text(
            "INSERT INTO McpServerRegistry (id, name, risk_tier) VALUES (:id, :name, :tier)"
        ), {"id": sid, "name": name, "tier": tier})

    now = datetime.utcnow()
    for days_ago in [60, 65, 70, 75]:
        ts = now - timedelta(days=days_ago)
        for sid in range(1, 6):
            session.execute(text(
                "INSERT INTO McpLlmAxisScore (server_id, metric_axis, p_top, recorded_at) VALUES (:sid, 'overall_risk', :score, :ts)"
            ), {"sid": sid, "score": 0.5, "ts": ts})

    for sid, old_score, new_score in [(1, 0.8, 0.5), (2, 0.7, 0.3), (3, 0.6, 0.4), (4, 0.3, 0.8), (5, 0.5, 0.5)]:
        ts = now - timedelta(days=5)
        session.execute(text(
            "INSERT INTO McpLlmAxisScore (server_id, metric_axis, p_top, recorded_at) VALUES (:sid, 'overall_risk', :score, :ts)"
        ), {"sid": sid, "score": new_score, "ts": ts})
        ts_old = now - timedelta(days=45)
        session.execute(text(
            "INSERT INTO McpLlmAxisScore (server_id, metric_axis, p_top, recorded_at) VALUES (:sid, 'overall_risk', :score, :ts)"
        ), {"sid": sid, "score": old_score, "ts": ts_old})

    session.commit()


def main():
    test_session = create_test_session()
    seed_test_data(test_session)

    app.dependency_overrides[get_session] = lambda: test_session
    client = TestClient(app)

    response = client.get("/api/risk/improvement-regression?window_days=60")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    response_data = response.json()
    assert len(response_data["improved"]) >= 1, f"Expected at least one improved entry, got {len(response_data['improved'])}"
    assert len(response_data["regressed"]) >= 1, f"Expected at least one regressed entry, got {len(response_data['regressed'])}"

    assert response_data["improved"][0]["delta"] > 0.1, f"Expected delta > 0.1 for improved server, got {response_data['improved'][0]['delta']}"
    assert response_data["regressed"][0]["delta"] < -0.1, f"Expected delta < -0.1 for regressed server, got {response_data['regressed'][0]['delta']}"

    assert response_data["unchanged"] >= 0
    assert response_data["total"] == len(response_data["improved"]) + len(response_data["regressed"]) + response_data["unchanged"]

    print("PASS")


if __name__ == "__main__":
    main()