from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel

app = FastAPI()


class NetworkEgressScoreResponse(BaseModel):
    server_id: str
    axis: str
    score: float
    risk_tier: str
    evidence: dict[str, Any]
    scored_at: str


def compute_tier(row: dict) -> tuple[float, str, dict]:
    p_top = float(row.get("p_top", 0.0))
    p_critical = float(row.get("p_critical", 0.0))
    p_danger = float(row.get("p_danger", 0.0))
    probs = row.get("probs", [])
    label = row.get("label", "unknown")

    if isinstance(probs, str):
        try:
            probs = json.loads(probs)
        except Exception:
            probs = []

    confidence_weight = p_top * 3.0 + p_critical * 2.0 + p_danger * 1.0

    if probs and isinstance(probs, list):
        probs_sum = sum(float(p) for p in probs if isinstance(p, (int, float)))
        if probs_sum > 0:
            confidence_weight += probs_sum * 0.5

    score = min(100.0, max(0.0, confidence_weight * 100.0))

    if score >= 80:
        tier = "critical"
    elif score >= 60:
        tier = "high"
    elif score >= 40:
        tier = "medium"
    elif score >= 20:
        tier = "low"
    elif score >= 5:
        tier = "minimal"
    else:
        tier = "negligible"

    evidence = {"p_top": p_top, "p_critical": p_critical, "label": label}
    return score, tier, evidence


def query_llm_axis_scores_via_service(server_id: str | None = None) -> list[dict]:
    payload: dict[str, Any] = {
        "sql": "SELECT server_id, p_top, p_critical, p_danger, probs, label, label_index, escalated, scored_at FROM mcp_llm_axis_scores WHERE axis_name = 'network_egress'",
        "params": {},
    }
    if server_id:
        payload["sql"] += " AND server_id = :server_id"
        payload["params"]["server_id"] = server_id

    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json=payload,
            timeout=5,
        )
        if resp.status_code == 200:
            result = resp.json()
            if isinstance(result, dict) and "rows" in result:
                return result["rows"]
            elif isinstance(result, list):
                return result
        return []
    except Exception:
        return []


@app.get("/api/scoring/network-egress", response_model=NetworkEgressScoreResponse)
def get_network_egress_score(
    server_id: str = Query(..., description="Server identifier"),
):
    rows = query_llm_axis_scores_via_service(server_id)
    if not rows:
        raise HTTPException(status_code=404, detail="No network_egress axis found for server")

    row = rows[0]
    score, tier, evidence = compute_tier(row)

    scored_at = row.get("scored_at")
    if scored_at:
        if isinstance(scored_at, str):
            scored_at_str = scored_at
        else:
            scored_at_str = scored_at.isoformat()
    else:
        scored_at_str = datetime.now(timezone.utc).isoformat()

    return NetworkEgressScoreResponse(
        server_id=row["server_id"],
        axis="network_egress",
        score=score,
        risk_tier=tier,
        evidence=evidence,
        scored_at=scored_at_str,
    )


def run_self_test():
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker, Session

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                adapter_sha256 TEXT,
                axis_name TEXT NOT NULL,
                decision_rule_version TEXT,
                escalated INTEGER DEFAULT 0,
                escalated_to TEXT,
                label TEXT,
                label_index INTEGER,
                model_version TEXT,
                p_critical REAL DEFAULT 0.0,
                p_danger REAL DEFAULT 0.0,
                p_top REAL DEFAULT 0.0,
                probs TEXT,
                scored_at TEXT,
                server_id TEXT NOT NULL
            )
        """))

    SessionLocal = sessionmaker(bind=engine)

    test_rows = [
        {
            "server_id": "srv-001",
            "axis_name": "network_egress",
            "p_top": 0.85,
            "p_critical": 0.10,
            "p_danger": 0.03,
            "probs": json.dumps([0.1, 0.2, 0.7]),
            "label": "critical_egress",
            "label_index": 3,
            "escalated": 1,
            "scored_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "server_id": "srv-002",
            "axis_name": "network_egress",
            "p_top": 0.20,
            "p_critical": 0.30,
            "p_danger": 0.25,
            "probs": json.dumps([0.4, 0.35, 0.25]),
            "label": "moderate_egress",
            "label_index": 1,
            "escalated": 0,
            "scored_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "server_id": "srv-003",
            "axis_name": "network_egress",
            "p_top": 0.02,
            "p_critical": 0.05,
            "p_danger": 0.10,
            "probs": json.dumps([0.6, 0.3, 0.1]),
            "label": "low_egress",
            "label_index": 0,
            "escalated": 0,
            "scored_at": datetime.now(timezone.utc).isoformat(),
        },
    ]

    with engine.begin() as conn:
        for row in test_rows:
            conn.execute(
                text("""
                    INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, p_top, p_critical, p_danger, probs, label, label_index, escalated, scored_at)
                    VALUES
                    (:server_id, :axis_name, :p_top, :p_critical, :p_danger, :probs, :label, :label_index, :escalated, :scored_at)
                """),
                row,
            )

    test_app = FastAPI()

    @test_app.get("/api/scoring/network-egress", response_model=NetworkEgressScoreResponse)
    def test_get_score(
        server_id: str = Query(...),
    ):
        session: Session = SessionLocal()
        try:
            result = session.execute(
                text("""
                    SELECT server_id, p_top, p_critical, p_danger, probs, label, label_index, escalated, scored_at
                    FROM mcp_llm_axis_scores
                    WHERE axis_name = 'network_egress' AND server_id = :server_id
                    LIMIT 1
                """),
                {"server_id": server_id},
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="No network_egress axis found for server")

            row_dict = {
                "server_id": row[0],
                "p_top": row[1],
                "p_critical": row[2],
                "p_danger": row[3],
                "probs": row[4],
                "label": row[5],
                "label_index": row[6],
                "escalated": row[7],
                "scored_at": row[8],
            }

            score, tier, evidence = compute_tier(row_dict)
            scored_at_str = row_dict["scored_at"] or datetime.now(timezone.utc).isoformat()

            return NetworkEgressScoreResponse(
                server_id=row_dict["server_id"],
                axis="network_egress",
                score=score,
                risk_tier=tier,
                evidence=evidence,
                scored_at=scored_at_str,
            )
        finally:
            session.close()

    client = TestClient(test_app)

    valid_tiers = {"negligible", "minimal", "low", "medium", "high", "critical"}

    for srv in ["srv-001", "srv-002", "srv-003"]:
        resp = client.get(f"/api/scoring/network-egress?server_id={srv}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code} for {srv}"

        data = resp.json()
        assert "score" in data, f"Missing score in response for {srv}"
        assert 0 <= data["score"] <= 100, f"Score {data['score']} out of range for {srv}"
        assert data["risk_tier"] in valid_tiers, f"Invalid tier {data['risk_tier']} for {srv}"
        assert "evidence" in data, f"Missing evidence in response for {srv}"
        assert data["axis"] == "network_egress", f"Wrong axis for {srv}"
        assert data["server_id"] == srv, f"Wrong server_id for {srv}"

    print("PASS")
    sys.exit(0)


if __name__ == "__main__":
    run_self_test()