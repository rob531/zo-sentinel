from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

THRESHOLDS = {"critical": 0.7, "high": 0.4, "medium": 0.2}
WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"
HEALTH_INTERVAL = 60


def compute_score(metadata: dict) -> tuple[float, dict]:
    p_top = metadata.get("p_top", 0.0)
    tier = "low"
    if p_top >= THRESHOLDS["critical"]:
        tier = "critical"
    elif p_top >= THRESHOLDS["high"]:
        tier = "high"
    elif p_top >= THRESHOLDS["medium"]:
        tier = "medium"
    score = p_top
    return score, {"risk_tier": tier}


def run():
    consumer = _AuthStrengthScoringConsumer()
    try:
        consumer.loop()
    except KeyboardInterrupt:
        consumer.shutdown()


class _AuthStrengthScoringConsumer:
    def __init__(self):
        self._running = True
        self._last_health = datetime.now(timezone.utc)

    def loop(self):
        while self._running:
            self._process_scores()
            self._check_heartbeat()

    def shutdown(self):
        self._running = False

    def _process_scores(self):
        for row in self._fetch_pending_scores():
            self._update_server_risk(row)

    def _fetch_pending_scores(self):
        with get_session() as sess:
            rows = (
                sess.query(McpLlmAxisScore)
                .filter(McpLlmAxisScore.axis_name == "auth_strength")
                .all()
            )
            sess.expunge_all()
            return rows

    def _update_server_risk(self, row: McpLlmAxisScore):
        metadata = {"p_top": row.p_top}
        _, derived = compute_score(metadata)
        tier = derived["risk_tier"]
        confidence = {"critical": 0.95, "high": 0.75, "medium": 0.5, "low": 0.25}[tier]
        server_id = row.server_id
        payload = {
            "server_id": server_id,
            "risk_tier": tier,
            "confidence": confidence,
            "last_assessed": datetime.now(timezone.utc).isoformat(),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
        try:
            requests.post(
                WRITE_SERVICE_URL,
                json={"table": "mcp_server_registry", "data": payload},
                timeout=5,
            )
        except requests.RequestException:
            pass

    def _check_heartbeat(self):
        now = datetime.now(timezone.utc)
        if (now - self._last_health).total_seconds() >= HEALTH_INTERVAL:
            self._last_health = now
            try:
                requests.post(
                    f"{WRITE_SERVICE_URL.replace('/query', '')}/health",
                    json={"service": "scoring_consumer_auth_strength", "ts": now.isoformat()},
                    timeout=5,
                )
            except requests.RequestException:
                pass


if __name__ == "__main__":
    import threading
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                label TEXT,
                label_index INTEGER,
                p_top REAL NOT NULL,
                p_critical REAL,
                p_danger REAL,
                probs TEXT,
                scored_at TIMESTAMP,
                escalated INTEGER,
                escalated_to TEXT,
                decision_rule_version TEXT,
                model_version TEXT,
                adapter_sha256 TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                risk_tier TEXT,
                confidence REAL,
                last_assessed TIMESTAMP,
                last_seen TIMESTAMP,
                name TEXT,
                url TEXT,
                description TEXT,
                registry_source TEXT,
                trust_score REAL,
                verdict TEXT,
                verdict_reasoning TEXT,
                meta TEXT,
                first_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                scan_count INTEGER
            )
        """))

    SessionLocal = sessionmaker(bind=engine)

    def seed_data():
        with SessionLocal() as sess:
            sess.execute(
                text("""
                INSERT INTO mcp_llm_axis_scores (id, server_id, axis_name, p_top, scored_at)
                VALUES (:id, :server_id, :axis_name, :p_top, :scored_at)
                """),
                [
                    {"id": 1, "server_id": "s1", "axis_name": "auth_strength", "p_top": 0.9, "scored_at": datetime.now(timezone.utc)},
                    {"id": 2, "server_id": "s2", "axis_name": "auth_strength", "p_top": 0.5, "scored_at": datetime.now(timezone.utc)},
                    {"id": 3, "server_id": "s3", "axis_name": "auth_strength", "p_top": 0.3, "scored_at": datetime.now(timezone.utc)},
                    {"id": 4, "server_id": "s4", "axis_name": "auth_strength", "p_top": 0.1, "scored_at": datetime.now(timezone.utc)},
                ],
            )
            sess.commit()

    seed_data()

    that_app = FastAPI()

    def override_get_session():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    that_app.dependency_overrides[get_session] = override_get_session

    _orig_get_session = get_session

    import app_scoring_consumer_auth_strength as m

    m.get_session = override_get_session

    _, d1 = m.compute_score({"p_top": 0.9})
    assert d1["risk_tier"] == "critical", f"expected critical, got {d1['risk_tier']}"

    _, d2 = m.compute_score({"p_top": 0.5})
    assert d2["risk_tier"] == "high", f"expected high, got {d2['risk_tier']}"

    _, d3 = m.compute_score({"p_top": 0.3})
    assert d3["risk_tier"] == "medium", f"expected medium, got {d3['risk_tier']}"

    _, d4 = m.compute_score({"p_top": 0.1})
    assert d4["risk_tier"] == "low", f"expected low, got {d4['risk_tier']}"

    print("PASS")