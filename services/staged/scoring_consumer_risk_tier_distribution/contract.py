from typing import Any
from collections import defaultdict
import requests
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore


WRITE_SERVICE_URL = "http://127.0.0.1:8772"


def compute_tier(p_top: float) -> str:
    if p_top > 0.75:
        return "TRUSTED_GENERAL"
    elif p_top > 0.60:
        return "TRUSTED_RESEARCH"
    elif p_top > 0.45:
        return "ENTERPRISE_CONTROLLED"
    elif p_top > 0.30:
        return "CAUTION_LIMITED"
    elif p_top > 0.15:
        return "HIGH_RISK_ISOLATED"
    else:
        return "KNOWN_THREAT"


def update_all_tiers(session: Session) -> dict[str, Any]:
    result = {
        "processed": 0,
        "tier_counts": defaultdict(int),
        "errors": []
    }

    server_ids = session.query(McpLlmAxisScore.server_id).distinct().all()
    server_ids = [s[0] for s in server_ids]

    for server_id in server_ids:
        try:
            axis_score = session.query(McpLlmAxisScore).filter(
                McpLlmAxisScore.server_id == server_id,
                McpLlmAxisScore.axis_name == "overall_risk"
            ).first()

            if axis_score is None:
                result["errors"].append(f"No overall_risk score for {server_id}")
                continue

            tier = compute_tier(axis_score.p_top)

            payload = {
                "sql": "UPDATE mcp_server_registry SET risk_tier = ?, last_assessed = NOW() WHERE server_id = ?",
                "params": [tier, server_id]
            }

            resp = requests.post(
                f"{WRITE_SERVICE_URL}/execute",
                json=payload,
                timeout=10
            )
            resp.raise_for_status()

            result["processed"] += 1
            result["tier_counts"][tier] += 1

        except Exception as e:
            result["errors"].append(f"Error processing {server_id}: {str(e)}")

    result["tier_counts"] = dict(result["tier_counts"])
    return result


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                probs TEXT,
                label TEXT,
                label_index INTEGER,
                model_version TEXT,
                decision_rule_version TEXT,
                adapter_sha256 TEXT,
                escalated INTEGER,
                escalated_to TEXT,
                scored_at TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                risk_tier TEXT,
                last_assessed TEXT,
                name TEXT,
                url TEXT,
                description TEXT,
                registry_source TEXT,
                trust_score REAL,
                confidence REAL,
                verdict TEXT,
                verdict_reasoning TEXT,
                first_seen TEXT,
                last_seen TEXT,
                last_scanned TEXT,
                scan_count INTEGER,
                meta TEXT
            )
        """))

    SessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    session = SessionLocal()
    session.add(McpLlmAxisScore(server_id="srv1", axis_name="overall_risk", p_top=0.80))
    session.add(McpLlmAxisScore(server_id="srv2", axis_name="overall_risk", p_top=0.80))
    session.add(McpLlmAxisScore(server_id="srv3", axis_name="overall_risk", p_top=0.35))
    session.add(McpLlmAxisScore(server_id="srv3", axis_name="other_axis", p_top=0.50))
    session.add(McpLlmAxisScore(server_id="srv4", axis_name="overall_risk", p_top=0.35))
    session.add(McpLlmAxisScore(server_id="srv5", axis_name="overall_risk", p_top=0.85))
    session.commit()
    session.close()

    original_post = requests.post
    captured_calls = []

    def mock_post(url, **kwargs):
        captured_calls.append((url, kwargs))
        class MockResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"success": True}
        return MockResponse()

    requests.post = mock_post

    try:
        result = update_all_tiers(app.state._get_session() if hasattr(app.state, '_get_session') else SessionLocal())
    except Exception:
        result = update_all_tiers(SessionLocal())

    requests.post = original_post

    assert result["processed"] == 5, f"Expected 5 processed, got {result['processed']}"
    assert result["tier_counts"].get("TRUSTED_GENERAL", 0) == 3, f"Expected 3 TRUSTED_GENERAL, got {result['tier_counts'].get('TRUSTED_GENERAL', 0)}"
    assert result["tier_counts"].get("CAUTION_LIMITED", 0) == 2, f"Expected 2 CAUTION_LIMITED, got {result['tier_counts'].get('CAUTION_LIMITED', 0)}"

    print("PASS")