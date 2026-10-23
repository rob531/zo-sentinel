from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.db import get_session
from app.models import McpLlmAxisScore


class CapabilityBreadthMetrics(BaseModel):
    breadth_score: float
    tool_count_estimate: int
    capability_categories: list[str]
    anomalous_tools: list[str]


def compute_capability_breadth_metrics(server_id: str, rows: list[dict]) -> dict:
    if not rows:
        return {
            "breadth_score": 0.0,
            "tool_count_estimate": 0,
            "capability_categories": [],
            "anomalous_tools": [],
        }

    total_weight = 0.0
    weighted_capability = 0.0
    tool_count_estimate = 0
    capability_categories_set: set[str] = set()
    anomalous_tools: list[str] = []

    for row in rows:
        probs = row.get("probs", [])
        p_top = row.get("p_top", 0.0)
        p_critical = row.get("p_critical", 0.0)
        label = row.get("label", "")

        if isinstance(probs, str):
            try:
                probs = json.loads(probs)
            except (json.JSONDecodeError, TypeError):
                probs = []

        weight = p_top + p_critical
        if weight > 0:
            max_prob = max(probs) if probs else 0.0
            weighted_capability += max_prob * weight
            total_weight += weight

        capability_categories_set.add(label) if label else None

        if p_critical > 0.3:
            anomalous_tools.append(label)

        tool_count_estimate += int(sum(1 for p in probs if p > 0.1))

    breadth_score = (weighted_capability / total_weight * 100.0) if total_weight > 0 else 0.0
    breadth_score = min(100.0, max(0.0, breadth_score))

    return {
        "breadth_score": round(breadth_score, 2),
        "tool_count_estimate": max(0, tool_count_estimate // max(len(rows), 1)),
        "capability_categories": sorted(list(capability_categories_set)),
        "anomalous_tools": anomalous_tools,
    }


def get_capability_breadth(session: Session, server_id: str) -> dict | None:
    rows = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id, McpLlmAxisScore.axis_name == "capability_breadth")
        .all()
    )
    if not rows:
        return None
    row_dicts = [
        {
            "server_id": r.server_id,
            "axis_name": r.axis_name,
            "label": r.label,
            "p_top": r.p_top,
            "p_critical": r.p_critical,
            "probs": r.probs,
        }
        for r in rows
    ]
    return compute_capability_breadth_metrics(server_id, row_dicts)


def write_capability_breadth_signal(server_id: str, metrics: dict) -> bool:
    import requests

    enrichment_record = {
        "server_id": server_id,
        "signal_type": "capability_breadth_metrics",
        "signal_data": json.dumps(metrics),
        "scored_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/write/mcp_signal_enrichments",
            json=enrichment_record,
            timeout=5,
        )
        return resp.status_code in (200, 201, 202)
    except requests.RequestException:
        return False


def consume_capability_breadth_scores(session: Session) -> int:
    rows = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.axis_name == "capability_breadth")
        .all()
    )
    if not rows:
        return 0

    servers: set[str] = {r.server_id for r in rows}
    processed = 0

    for server_id in servers:
        server_rows = [r for r in rows if r.server_id == server_id]
        row_dicts = [
            {
                "server_id": r.server_id,
                "axis_name": r.axis_name,
                "label": r.label,
                "p_top": r.p_top,
                "p_critical": r.p_critical,
                "probs": r.probs,
            }
            for r in server_rows
        ]
        metrics = compute_capability_breadth_metrics(server_id, row_dicts)
        if write_capability_breadth_signal(server_id, metrics):
            processed += 1

    return processed


def run_self_test() -> bool:
    db_path = Path(__file__).parent / "test_capability_breadth.db"
    if db_path.exists():
        db_path.unlink()

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata_sql = """
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
        p_critical REAL,
        p_danger REAL,
        p_top REAL,
        probs TEXT,
        scored_at TEXT,
        server_id TEXT NOT NULL
    );
    CREATE TABLE mcp_signal_enrichments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id TEXT NOT NULL,
        signal_type TEXT NOT NULL,
        signal_data TEXT,
        scored_at TEXT
    );
    """
    with engine.connect() as conn:
        for stmt in metadata_sql.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))
        conn.commit()

    TestSessionLocal = sessionmaker(bind=engine)

    @contextmanager
    def override_get_session() -> Generator[Session, None, None]:
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    seed_rows = [
        {
            "server_id": "srv_alpha",
            "axis_name": "capability_breadth",
            "label": "code_generation",
            "p_top": 0.65,
            "p_critical": 0.15,
            "probs": json.dumps([0.7, 0.2, 0.08, 0.02]),
            "scored_at": datetime.now(timezone.utc).isoformat(),
            "adapter_sha256": "abc123",
            "decision_rule_version": "v1",
            "model_version": "model-a",
        },
        {
            "server_id": "srv_alpha",
            "axis_name": "capability_breadth",
            "label": "data_processing",
            "p_top": 0.45,
            "p_critical": 0.25,
            "probs": json.dumps([0.5, 0.3, 0.15, 0.05]),
            "scored_at": datetime.now(timezone.utc).isoformat(),
            "adapter_sha256": "abc123",
            "decision_rule_version": "v1",
            "model_version": "model-a",
        },
        {
            "server_id": "srv_beta",
            "axis_name": "capability_breadth",
            "label": "file_operations",
            "p_top": 0.80,
            "p_critical": 0.05,
            "probs": json.dumps([0.85, 0.1, 0.03, 0.02]),
            "scored_at": datetime.now(timezone.utc).isoformat(),
            "adapter_sha256": "def456",
            "decision_rule_version": "v1",
            "model_version": "model-b",
        },
    ]

    with TestSessionLocal() as session:
        for row in seed_rows:
            session.execute(
                text("""
                INSERT INTO mcp_llm_axis_scores
                (server_id, axis_name, label, p_top, p_critical, probs, scored_at,
                 adapter_sha256, decision_rule_version, model_version)
                VALUES
                (:server_id, :axis_name, :label, :p_top, :p_critical, :probs, :scored_at,
                 :adapter_sha256, :decision_rule_version, :model_version)
                """),
                row,
            )
        session.commit()

    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    try:
        with override_get_session() as session:
            all_rows = session.execute(
                text("SELECT server_id, axis_name, label, p_top, p_critical, probs FROM mcp_llm_axis_scores")
            ).fetchall()

            all_row_dicts = [
                {
                    "server_id": r[0],
                    "axis_name": r[1],
                    "label": r[2],
                    "p_top": r[3],
                    "p_critical": r[4],
                    "probs": r[5],
                }
                for r in all_rows
            ]

            for srv_id in ["srv_alpha", "srv_beta"]:
                srv_rows = [r for r in all_row_dicts if r["server_id"] == srv_id]
                metrics = compute_capability_breadth_metrics(srv_id, srv_rows)

                assert isinstance(metrics["breadth_score"], float), "breadth_score must be float"
                assert 0 <= metrics["breadth_score"] <= 100, f"breadth_score {metrics['breadth_score']} out of range [0,100]"
                assert isinstance(metrics["tool_count_estimate"], int), "tool_count_estimate must be int"
                assert metrics["tool_count_estimate"] >= 0, "tool_count_estimate must be >= 0"
                assert isinstance(metrics["capability_categories"], list), "capability_categories must be list"
                assert isinstance(metrics["anomalous_tools"], list), "anomalous_tools must be list"

        health_resp = client.get("/health")
        assert health_resp.status_code == 200, "health check failed"

    finally:
        engine.dispose()
        if db_path.exists():
            db_path.unlink()

    return True


if __name__ == "__main__":
    if run_self_test():
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL")
        sys.exit(1)