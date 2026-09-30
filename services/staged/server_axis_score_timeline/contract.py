from datetime import date, datetime, timedelta
from typing import List, Dict, Any
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

AXIS_NAMES = ["auth_strength", "capability_breadth", "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface"]
AXIS_LABEL_INDICES = list(range(1, 7))

app = FastAPI()

class AxisScoresResponse(BaseModel):
    server_id: str
    name: str
    days: int
    series: List[Dict[str, Any]]

@app.get("/api/servers/{server_id}/axis-timeline", response_model=AxisScoresResponse)
def get_server_axis_timeline(
    server_id: str,
    days: int = 30,
    db: Session = Depends(get_session)
) -> AxisScoresResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    query = text("""
        SELECT 
            DATE(s.scored_at) as score_date,
            s.label_index,
            AVG(s.p_top) as avg_p_top
        FROM mcp_llm_axis_scores s
        JOIN mcp_server_registry r ON s.server_id = r.server_id
        WHERE s.server_id = :server_id
          AND s.scored_at >= :cutoff
          AND s.label_index BETWEEN 1 AND 6
        GROUP BY DATE(s.scored_at), s.label_index
        ORDER BY score_date ASC, s.label_index ASC
    """)
    
    result = db.execute(query, {"server_id": server_id, "cutoff": cutoff}).fetchall()
    
    server_query = text("SELECT name FROM mcp_server_registry WHERE server_id = :server_id")
    server_row = db.execute(server_query, {"server_id": server_id}).fetchone()
    
    if not server_row:
        raise HTTPException(status_code=404, detail="Server not found")
    
    name = server_row[0]
    
    series_dict: Dict[str, Dict[str, float]] = {}
    for row in result:
        score_date, label_index, avg_p_top = row
        if isinstance(score_date, datetime):
            score_date = score_date.date()
        date_str = score_date.isoformat() if isinstance(score_date, date) else str(score_date)
        
        if date_str not in series_dict:
            series_dict[date_str] = {}
        
        axis_name = AXIS_NAMES[label_index - 1]
        series_dict[date_str][axis_name] = float(avg_p_top)
    
    series = []
    for date_str in sorted(series_dict.keys()):
        series.append({"date": date_str, "axes": series_dict[date_str]})
    
    return AxisScoresResponse(
        server_id=server_id,
        name=name,
        days=days,
        series=series
    )

if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    create_tables = text("""
        CREATE TABLE mcp_server_registry (
            server_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            confidence REAL,
            description TEXT,
            first_seen TEXT,
            last_assessed TEXT,
            last_scanned TEXT,
            last_seen TEXT,
            meta TEXT,
            registry_source TEXT,
            risk_tier TEXT,
            scan_count INTEGER,
            trust_score REAL,
            url TEXT,
            verdict TEXT,
            verdict_reasoning TEXT
        )
    """)
    
    create_axis_scores = text("""
        CREATE TABLE mcp_llm_axis_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT NOT NULL,
            adapter_sha256 TEXT,
            axis_name TEXT,
            decision_rule_version TEXT,
            escalated INTEGER,
            escalated_to TEXT,
            label TEXT,
            label_index INTEGER,
            model_version TEXT,
            p_critical REAL,
            p_danger REAL,
            p_top REAL,
            probs TEXT,
            scored_at TEXT NOT NULL,
            FOREIGN KEY (server_id) REFERENCES mcp_server_registry(server_id)
        )
    """)
    
    with test_engine.connect() as conn:
        conn.execute(create_tables)
        conn.execute(create_axis_scores)
        conn.commit()
    
    TestSession = sessionmaker(bind=test_engine)
    
    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()
    
    base_date = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    
    servers_data = [
        ("srv-001", "Test Server Alpha"),
        ("srv-002", "Test Server Beta")
    ]
    
    scores_data = []
    for i, (server_id, name) in enumerate(servers_data):
        for day_offset in range(3):
            for axis_idx in range(1, 7):
                scored_at = base_date - timedelta(days=2 - day_offset)
                p_top_value = 0.5 + (axis_idx * 0.05) + (day_offset * 0.1)
                scores_data.append({
                    "server_id": server_id,
                    "scored_at": scored_at.isoformat(),
                    "label_index": axis_idx,
                    "p_top": p_top_value
                })
    
    with test_engine.connect() as conn:
        conn.execute(text("INSERT INTO mcp_server_registry (server_id, name) VALUES (:server_id, :name)"),
                     [{"server_id": srv[0], "name": srv[1]} for srv in servers_data])
        
        for score in scores_data:
            conn.execute(text("""
                INSERT INTO mcp_llm_axis_scores (server_id, scored_at, label_index, p_top, axis_name, label, adapter_sha256)
                VALUES (:server_id, :scored_at, :label_index, :p_top, :axis_name, :label, :adapter_sha256)
            """), {
                **score,
                "axis_name": AXIS_NAMES[score["label_index"] - 1],
                "label": f"axis_{score['label_index']}",
                "adapter_sha256": "abc123"
            })
        conn.commit()
    
    test_app = FastAPI()
    test_app.include_router(app.router)
    test_app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(test_app)
    
    response = client.get("/api/servers/srv-001/axis-timeline?days=30")
    
    if response.status_code != 200:
        print(f"FAIL: Expected 200, got {response.status_code}")
        sys.exit(1)
    
    data = response.json()
    
    if len(data["series"]) != 3:
        print(f"FAIL: Expected series length 3, got {len(data['series'])}")
        sys.exit(1)
    
    p_top_found = False
    for item in data["series"]:
        if "auth_strength" in item["axes"]:
            p_top_val = item["axes"]["auth_strength"]
            if abs(p_top_val - 0.55) < 0.01:
                p_top_found = True
                break
    
    if not p_top_found:
        print(f"FAIL: Expected known p_top value 0.55 for auth_strength, got: {data}")
        sys.exit(1)
    
    print("PASS")
    sys.exit(0)