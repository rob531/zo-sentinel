from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()

AXIS_NAMES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]

TIER_THRESHOLDS = [
    (75, "TRUSTED_GENERAL"),
    (60, "TRUSTED_RESEARCH"),
    (45, "ENTERPRISE_CONTROLLED"),
    (30, "CAUTION_LIMITED"),
    (15, "HIGH_RISK_ISOLATED"),
    (float("-inf"), "KNOWN_THREAT"),
]

DECISION_RULE_VERSION = "1.0.0"


def compute_composite_score(axis_scores: List[Dict[str, Any]]) -> Optional[float]:
    if not axis_scores:
        return None
    total = 0.0
    for score in axis_scores:
        p_danger = score.get("p_danger") or 0.0
        p_critical = score.get("p_critical") or 0.0
        score_val = 100 * (1.0 - p_danger - (0.5 * p_critical))
        total += max(0.0, min(100.0, score_val))
    return round(total / len(axis_scores), 2)


def assign_risk_tier(composite_score: Optional[float], axis_count: int) -> str:
    if axis_count >= 5 and composite_score is None:
        return "INSUFFICIENT"
    if axis_count < 5:
        return "INSUFFICIENT"
    for threshold, tier in TIER_THRESHOLDS:
        if composite_score > threshold:
            return tier
    return "KNOWN_THREAT"


class RiskTierResponse(BaseModel):
    server_id: str
    risk_tier: str
    axis_count: int
    composite_score: Optional[float]
    decision_rule_version: str
    scored_at: datetime


class RiskTierAssignPayload(BaseModel):
    server_id: str


def query_axis_scores(server_id: str, write_service_url: str) -> List[Dict[str, Any]]:
    import httpx

    query_sql = text("""
        SELECT axis_name, p_danger, p_critical, p_top, label, scored_at
        FROM McpLlmAxisScore
        WHERE server_id = :server_id
    """)
    payload = {
        "statements": [
            {"sql": str(query_sql), "params": {"server_id": server_id}}
        ]
    }
    try:
        response = httpx.post(f"{write_service_url}/query", json=payload, timeout=10.0)
        response.raise_for_status()
        result = response.json()
        rows = result.get("results", [{}])[0].get("rows", [])
        return rows
    except Exception:
        return []


def execute_write(
    sql: str,
    params: Dict[str, Any],
    write_service_url: str,
) -> Dict[str, Any]:
    import httpx

    payload = {
        "statements": [{"sql": sql, "params": params}]
    }
    try:
        response = httpx.post(f"{write_service_url}/execute", json=payload, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Write service error: {e}")


@router.get("/api/servers/{server_id}/risk-tier-assign", response_model=RiskTierResponse)
def get_risk_tier_assign(
    server_id: str,
    write_service_url: str = "http://127.0.0.1:8772",
):
    axis_scores = query_axis_scores(server_id, write_service_url)
    axis_count = len([s for s in axis_scores if s.get("axis_name") in AXIS_NAMES])
    composite_score = compute_composite_score(axis_scores)
    risk_tier = assign_risk_tier(composite_score, axis_count)
    scored_at = datetime.now(timezone.utc)

    return RiskTierResponse(
        server_id=server_id,
        risk_tier=risk_tier,
        axis_count=axis_count,
        composite_score=composite_score,
        decision_rule_version=DECISION_RULE_VERSION,
        scored_at=scored_at,
    )


@router.post("/api/servers/{server_id}/risk-tier-assign", response_model=RiskTierResponse)
def post_risk_tier_assign(
    server_id: str,
    write_service_url: str = "http://127.0.0.1:8772",
):
    axis_scores = query_axis_scores(server_id, write_service_url)
    axis_count = len([s for s in axis_scores if s.get("axis_name") in AXIS_NAMES])
    composite_score = compute_composite_score(axis_scores)
    risk_tier = assign_risk_tier(composite_score, axis_count)
    scored_at = datetime.now(timezone.utc)

    update_sql = text("""
        INSERT INTO McpServerRegistry (server_id, risk_tier, decision_rule_version, scored_at, last_assessed)
        VALUES (:server_id, :risk_tier, :decision_rule_version, :scored_at, :scored_at)
        ON CONFLICT (server_id) DO UPDATE SET
            risk_tier = EXCLUDED.risk_tier,
            decision_rule_version = EXCLUDED.decision_rule_version,
            scored_at = EXCLUDED.scored_at,
            last_assessed = EXCLUDED.last_assessed
    """)

    execute_write(
        str(update_sql),
        {
            "server_id": server_id,
            "risk_tier": risk_tier,
            "decision_rule_version": DECISION_RULE_VERSION,
            "scored_at": scored_at.isoformat(),
        },
        write_service_url,
    )

    return RiskTierResponse(
        server_id=server_id,
        risk_tier=risk_tier,
        axis_count=axis_count,
        composite_score=composite_score,
        decision_rule_version=DECISION_RULE_VERSION,
        scored_at=scored_at,
    )


def compute_risk_tier_percentages(
    db: Session,
) -> Dict[str, float]:
    results = db.execute(
        text("""
            SELECT risk_tier, COUNT(*) as cnt
            FROM McpServerRegistry
            WHERE risk_tier IS NOT NULL
            GROUP BY risk_tier
        """)
    ).fetchall()
    total = sum(r[1] for r in results)
    if total == 0:
        return {}
    return {r[0]: round(100.0 * r[1] / total, 2) for r in results}


def compute_severity_distribution(
    db: Session,
) -> Dict[str, int]:
    results = db.execute(
        text("""
            SELECT risk_tier, COUNT(*) as cnt
            FROM McpServerRegistry
            WHERE risk_tier IS NOT NULL
            GROUP BY risk_tier
            ORDER BY risk_tier
        """)
    ).fetchall()
    return {r[0]: r[1] for r in results}


def check_single_instance(db: Session, server_id: str) -> bool:
    result = db.execute(
        text("SELECT 1 FROM McpServerRegistry WHERE server_id = :server_id LIMIT 1"),
        {"server_id": server_id}
    ).fetchone()
    return result is not None


def get_server_risk_history(
    db: Session,
    server_id: str,
) -> List[Dict[str, Any]]:
    results = db.execute(
        text("""
            SELECT risk_tier, scored_at, decision_rule_version
            FROM McpServerRegistry
            WHERE server_id = :server_id AND risk_tier IS NOT NULL
            ORDER BY scored_at DESC
        """),
        {"server_id": server_id}
    ).fetchall()
    return [
        {"risk_tier": r[0], "scored_at": r[1], "decision_rule_version": r[2]}
        for r in results
    ]


def get_previous_snapshot(
    db: Session,
    server_id: str,
) -> Optional[Dict[str, Any]]:
    result = db.execute(
        text("""
            SELECT risk_tier, scored_at, decision_rule_version
            FROM McpServerRegistry
            WHERE server_id = :server_id AND risk_tier IS NOT NULL
            ORDER BY scored_at DESC
            LIMIT 1 OFFSET 1
        """),
        {"server_id": server_id}
    ).fetchone()
    if result:
        return {
            "risk_tier": result[0],
            "scored_at": result[1],
            "decision_rule_version": result[2],
        }
    return None


def signal_handler(server_id: str, db: Session) -> Dict[str, Any]:
    risk_tier = db.execute(
        text("SELECT risk_tier FROM McpServerRegistry WHERE server_id = :server_id"),
        {"server_id": server_id}
    ).scalar()
    return {"server_id": server_id, "risk_tier": risk_tier}


def compute_corpus_stats(db: Session) -> Dict[str, Any]:
    total = db.execute(text("SELECT COUNT(*) FROM McpServerRegistry")).scalar() or 0
    with_tier = db.execute(
        text("SELECT COUNT(*) FROM McpServerRegistry WHERE risk_tier IS NOT NULL")
    ).scalar() or 0
    return {"total_servers": total, "servers_with_tier": with_tier}


def fetch_enrichments_for_server(db: Session, server_id: str) -> List[Dict[str, Any]]:
    results = db.execute(
        text("""
            SELECT axis_name, label, p_danger, p_critical, scored_at
            FROM McpLlmAxisScore
            WHERE server_id = :server_id
        """),
        {"server_id": server_id}
    ).fetchall()
    return [
        {"axis_name": r[0], "label": r[1], "p_danger": r[2], "p_critical": r[3], "scored_at": r[4]}
        for r in results
    ]


def compute_heartbeat_age_seconds(last_seen: Optional[datetime]) -> Optional[float]:
    if last_seen is None:
        return None
    now = datetime.now(timezone.utc)
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return (now - last_seen).total_seconds()


def run(server_ids: List[str], db: Session) -> List[Dict[str, Any]]:
    results = []
    for sid in server_ids:
        tier = db.execute(
            text("SELECT risk_tier FROM McpServerRegistry WHERE server_id = :server_id"),
            {"server_id": sid}
        ).scalar()
        results.append({"server_id": sid, "risk_tier": tier})
    return results


def refresh_facets(db: Session) -> List[str]:
    results = db.execute(
        text("SELECT DISTINCT risk_tier FROM McpServerRegistry WHERE risk_tier IS NOT NULL")
    ).fetchall()
    return [r[0] for r in results]


def pipeline_events(pipeline_id: str, db: Session) -> List[Dict[str, Any]]:
    return []


def ensure_tables(db: Session) -> None:
    pass


def query_entity_report_contracts(db: Session, org_id: str) -> List[Dict[str, Any]]:
    return []


def export_entity_report_to_json(db: Session, report_id: str) -> Dict[str, Any]:
    return {}


def export_all_entity_reports_to_json(db: Session) -> List[Dict[str, Any]]:
    return []


def send_heartbeat(server_id: str, db: Session) -> bool:
    return True


def get_signal_histogram(db: Session) -> Dict[str, Any]:
    return {"buckets": []}


def get_trust_score_distribution(db: Session) -> Dict[str, Any]:
    return {"distribution": []}


def get_fingerprints_for_server(db: Session, server_id: str) -> List[str]:
    results = db.execute(
        text("SELECT adapter_sha256 FROM McpLlmAxisScore WHERE server_id = :server_id"),
        {"server_id": server_id}
    ).fetchall()
    return [r[0] for r in results if r[0]]


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool

    in_memory_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with in_memory_engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                server_id TEXT PRIMARY KEY,
                risk_tier TEXT,
                decision_rule_version TEXT,
                scored_at TIMESTAMP,
                last_assessed TIMESTAMP,
                trust_score REAL,
                name TEXT,
                url TEXT,
                description TEXT,
                registry_source TEXT,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                scan_count INTEGER,
                confidence REAL,
                meta TEXT,
                verdict TEXT,
                verdict_reasoning TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE McpLlmAxisScore (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT,
                axis_name TEXT,
                p_danger REAL,
                p_critical REAL,
                p_top REAL,
                probs TEXT,
                label TEXT,
                label_index INTEGER,
                model_version TEXT,
                decision_rule_version TEXT,
                escalated INTEGER,
                escalated_to TEXT,
                adapter_sha256 TEXT,
                scored_at TIMESTAMP
            )
        """))
        conn.commit()

    TestSession = sessionmaker(bind=in_memory_engine)
    test_db = TestSession()

    now_ts = datetime.now(timezone.utc).isoformat()

    test_db.execute(text("""
        INSERT INTO McpServerRegistry (server_id, name, risk_tier, last_assessed)
        VALUES ('srv-trusted', 'Trusted Server', 'TRUSTED_GENERAL', :now)
    """), {"now": now_ts})
    test_db.execute(text("""
        INSERT INTO McpServerRegistry (server_id, name, risk_tier, last_assessed)
        VALUES ('srv-enterprise', 'Enterprise Server', 'ENTERPRISE_CONTROLLED', :now)
    """), {"now": now_ts})
    test_db.execute(text("""
        INSERT INTO McpServerRegistry (server_id, name, risk_tier, last_assessed)
        VALUES ('srv-highrisk', 'High Risk Server', 'HIGH_RISK_ISOLATED', :now)
    """), {"now": now_ts})

    axis_data_trusted = [
        {"axis_name": "overall_risk", "p_danger": 0.05, "p_critical": 0.02},
        {"axis_name": "auth_strength", "p_danger": 0.08, "p_critical": 0.01},
        {"axis_name": "capability_breadth", "p_danger": 0.10, "p_critical": 0.02},
        {"axis_name": "data_sensitivity", "p_danger": 0.06, "p_critical": 0.01},
        {"axis_name": "network_egress", "p_danger": 0.07, "p_critical": 0.01},
        {"axis_name": "maintainer_trust", "p_danger": 0.09, "p_critical": 0.02},
        {"axis_name": "exploit_surface", "p_danger": 0.08, "p_critical": 0.01},
    ]

    axis_data_enterprise = [
        {"axis_name": "overall_risk", "p_danger": 0.35, "p_critical": 0.10},
        {"axis_name": "auth_strength", "p_danger": 0.40, "p_critical": 0.08},
        {"axis_name": "capability_breadth", "p_danger": 0.30, "p_critical": 0.12},
        {"axis_name": "data_sensitivity", "p_danger": 0.38, "p_critical": 0.10},
        {"axis_name": "network_egress", "p_danger": 0.32, "p_critical": 0.08},
        {"axis_name": "maintainer_trust", "p_danger": 0.36, "p_critical": 0.09},
        {"axis_name": "exploit_surface", "p_danger": 0.34, "p_critical": 0.10},
    ]

    axis_data_highrisk = [
        {"axis_name": "overall_risk", "p_danger": 0.75, "p_critical": 0.15},
        {"axis_name": "auth_strength", "p_danger": 0.80, "p_critical": 0.12},
        {"axis_name": "capability_breadth", "p_danger": 0.70, "p_critical": 0.18},
        {"axis_name": "data_sensitivity", "p_danger": 0.78, "p_critical": 0.14},
        {"axis_name": "network_egress", "p_danger": 0.72, "p_critical": 0.16},
        {"axis_name": "exploit_surface", "p_danger": 0.76, "p_critical": 0.13},
    ]

    mock_store: Dict[str, List[Dict[str, Any]]] = {
        "srv-trusted": axis_data_trusted,
        "srv-enterprise": axis_data_enterprise,
        "srv-highrisk": axis_data_highrisk,
    }

    class MockHTTPClient:
        def post(self, url: str, json=None, timeout=None):
            class MockResponse:
                def raise_for_status(self):
                    pass
                def json(self):
                    if "/query" in url:
                        statements = json.get("statements", [])
                        if statements:
                            params = statements[0].get("params", {})
                            sid = params.get("server_id", "")
                            rows = mock_store.get(sid, [])
                            return {"results": [{"rows": rows}]}
                    return {"results": []}
            return MockResponse()

    import httpx
    original_post = httpx.post
    httpx.post = lambda url, **kwargs: MockHTTPClient().post(url, **kwargs)

    app = FastAPI()
    app.include_router(router)

    class MockWriteService:
        def __init__(self):
            self.written: List[Dict[str, Any]] = []

        def query(self, server_id: str):
            return mock_store.get(server_id, [])

        def execute(self, sql: str, params: Dict[str, Any]):
            self.written.append({"sql": sql, "params": params})
            return {"success": True}

    mock_ws = MockWriteService()

    original_query = query_axis_scores
    original_execute = execute_write

    def mock_query(sid: str, wurl: str):
        return mock_store.get(sid, [])

    def mock_execute(sql: str, params: Dict[str, Any], wurl: str):
        mock_ws.execute(sql, params)
        return {"success": True}

    import services.staged.risk_tier_assign.logic as logic_module
    logic_module.query_axis_scores = mock_query
    logic_module.execute_write = mock_execute

    test_app = FastAPI()
    test_app.include_router(router)

    test_app.dependency_overrides[get_session] = lambda: test_db

    from fastapi.testclient import TestClient
    client = TestClient(test_app)

    passed = True
    expected_tiers = {
        "srv-trusted": "TRUSTED_GENERAL",
        "srv-enterprise": "ENTERPRISE_CONTROLLED",
        "srv-highrisk": "HIGH_RISK_ISOLATED",
    }

    tier_labels_seen = set()

    for sid, expected_tier in expected_tiers.items():
        response = client.get(f"/api/servers/{sid}/risk-tier-assign")
        if response.status_code != 200:
            print(f"FAIL: {sid} returned {response.status_code}")
            passed = False
            continue

        data = response.json()
        actual_tier = data.get("risk_tier")
        tier_labels_seen.add(actual_tier)

        if actual_tier != expected_tier:
            print(f"FAIL: {sid} expected {expected_tier}, got {actual_tier}")
            passed = False
        else:
            print(f"PASS: {sid} -> {actual_tier} (axis_count={data.get('axis_count')}, score={data.get('composite_score')})")

        response_post = client.post(f"/api/servers/{sid}/risk-tier-assign")
        if response_post.status_code != 200:
            print(f"FAIL: POST {sid} returned {response_post.status_code}")
            passed = False

    if len(tier_labels_seen) < 3:
        print(f"FAIL: Expected 3 distinct tier labels, got {len(tier_labels_seen)}: {tier_labels_seen}")
        passed = False

    httpx.post = original_post

    if passed:
        print("PASS")
    else:
        print("FAIL")