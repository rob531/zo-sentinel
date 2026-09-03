from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class AxisMetrics(BaseModel):
    axis_name: str
    precision: float
    recall: float
    f1_score: float
    divergence_count: int


class PrecisionAuditResponse(BaseModel):
    total_servers: int
    audited_at: str
    axes: list[AxisMetrics]
    overall_accuracy: float


def compute_precision_audit(session: Session) -> dict[str, Any]:
    audited_at = datetime.now(timezone.utc).isoformat()
    
    sql = text("""
        SELECT 
            ax.axis_name,
            COUNT(CASE WHEN ax.label = s.risk_tier THEN 1 END) as correct,
            COUNT(CASE WHEN ax.label != s.risk_tier THEN 1 END) as diverged,
            COUNT(*) as total,
            SUM(CASE WHEN ax.label = 'high' AND s.risk_tier = 'high' THEN 1 ELSE 0 END) as tp,
            SUM(CASE WHEN ax.label = 'high' AND s.risk_tier != 'high' THEN 1 ELSE 0 END) as fp,
            SUM(CASE WHEN ax.label != 'high' AND s.risk_tier = 'high' THEN 1 ELSE 0 END) as fn
        FROM mcp_llm_axis_scores ax
        JOIN mcp_server_registry s ON ax.server_id = s.server_id
        GROUP BY ax.axis_name
    """)
    
    result = session.execute(sql)
    rows = result.fetchall()
    
    total_servers = 0
    axes = []
    total_correct = 0
    total_count = 0
    
    for row in rows:
        axis_name = row[0]
        correct = row[1]
        diverged = row[2]
        total = row[3]
        tp = row[4] or 0
        fp = row[5] or 0
        fn = row[6] or 0
        
        total_servers = max(total_servers, total)
        total_correct += correct
        total_count += total
        
        precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        
        axes.append({
            "axis_name": axis_name,
            "precision": precision,
            "recall": recall,
            "f1_score": f1,
            "divergence_count": diverged
        })
    
    overall_accuracy = total_correct / total_count if total_count > 0 else 0.0
    
    return {
        "total_servers": total_servers,
        "audited_at": audited_at,
        "axes": axes,
        "overall_accuracy": overall_accuracy
    }


app = FastAPI()


@app.get("/api/scoring/precision-audit", response_model=PrecisionAuditResponse)
def get_precision_audit(session: Session = Depends(get_session)) -> dict[str, Any]:
    result = compute_precision_audit(session)
    return jsonable_encoder(result)


if __name__ == "__main__":
    import sys
    
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    with test_engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT,
                registry_source TEXT,
                risk_tier TEXT,
                trust_score REAL,
                confidence REAL,
                description TEXT,
                verdict TEXT,
                verdict_reasoning TEXT,
                meta TEXT,
                first_seen TEXT,
                last_seen TEXT,
                last_scanned TEXT,
                last_assessed TEXT,
                scan_count INTEGER
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT,
                axis_name TEXT,
                label TEXT,
                label_index INTEGER,
                probs TEXT,
                p_critical REAL,
                p_danger REAL,
                p_top REAL,
                model_version TEXT,
                decision_rule_version TEXT,
                escalated INTEGER,
                escalated_to TEXT,
                scored_at TEXT,
                FOREIGN KEY (server_id) REFERENCES mcp_server_registry(server_id)
            )
        """))
        conn.commit()
    
    TestingSessionLocal = sessionmaker(bind=test_engine)
    
    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    seeded_servers = [
        ("srv_001", "Server Alpha", "low"),
        ("srv_002", "Server Beta", "low"),
        ("srv_003", "Server Gamma", "low"),
        ("srv_004", "Server Delta", "low"),
        ("srv_005", "Server Epsilon", "low"),
        ("srv_006", "Server Zeta", "low"),
        ("srv_007", "Server Eta", "low"),
        ("srv_008", "Server Theta", "high"),
        ("srv_009", "Server Iota", "high"),
        ("srv_010", "Server Kappa", "high"),
    ]
    
    axis_distributions = [
        ("security", "low", "low"),
        ("security", "low", "low"),
        ("security", "low", "low"),
        ("security", "low", "low"),
        ("security", "low", "low"),
        ("security", "low", "low"),
        ("security", "low", "low"),
        ("security", "high", "high"),
        ("security", "high", "high"),
        ("security", "high", "high"),
    ]
    
    with test_engine.begin() as conn:
        for server_id, name, risk_tier in seeded_servers:
            conn.execute(
                text("""INSERT INTO mcp_server_registry 
                    (server_id, name, url, registry_source, risk_tier, trust_score, 
                     confidence, description, verdict, scan_count)
                    VALUES (:sid, :name, :url, 'test', :rt, 0.8, 0.9, 'test desc', 'ok', 1)"""),
                {"sid": server_id, "name": name, "url": f"http://{name.lower()}.test", "rt": risk_tier}
            )
        
        for i, (axis_name, expected_label, _) in enumerate(axis_distributions):
            conn.execute(
                text("""INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, label, label_index, probs, p_critical, 
                     p_danger, p_top, model_version, scored_at)
                    VALUES (:sid, :ax, :lbl, :idx, '[]', 0.1, 0.3, 0.6, 'v1', '2024-01-01')"""),
                {"sid": seeded_servers[i][0], "ax": axis_name, "lbl": expected_label, "idx": 0}
            )
        conn.commit()
    
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    
    response = client.get("/api/scoring/precision-audit")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert len(data.get("axes", [])) > 0, "axes array should not be empty"
    for axis in data.get("axes", []):
        assert 0 <= axis["f1_score"] <= 1, f"f1_score {axis['f1_score']} out of range [0,1]"
    
    print("PASS")
    sys.exit(0)