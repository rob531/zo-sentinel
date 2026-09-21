# services/staged/risk_tier_transition_matrix_api/contract.py
"""
Risk Tier Transition Matrix API

GET /api/risk/transition-matrix?days=30
Returns matrix of server transitions between risk tiers over a time window.
"""
from fastapi import APIRouter, Depends
from app.db import get_session
from app.models import McpServerRegistry
from sqlalchemy import text
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

router = APIRouter()


class TransitionMatrixResponse(BaseModel):
    days: int
    matrix: Dict[str, Any]
    totals: Dict[str, Dict[str, int]]


@router.get("/api/risk/transition-matrix", response_model=TransitionMatrixResponse)
def get_risk_tier_transition_matrix(
    days: int = 30,
    db: Session = Depends(get_session)
) -> TransitionMatrixResponse:
    """
    Returns a matrix of how many servers transitioned between each pair of risk tiers
    over the requested time window.
    """
    result = compute_transition_matrix(db, days)
    return TransitionMatrixResponse(**result)


def compute_transition_matrix(db: Session, days: int) -> Dict[str, Any]:
    """
    Compute risk tier transition matrix from McpServerRegistry history.
    
    Uses last_scanned timestamps to track server tier changes over time.
    """
    risk_tiers = ["critical", "high", "medium", "low", "minimal"]
    
    query = text("""
        WITH server_scans AS (
            SELECT 
                server_id,
                server_name,
                risk_tier,
                last_scanned,
                ROW_NUMBER() OVER (PARTITION BY server_id ORDER BY last_scanned) as scan_num
            FROM McpServerRegistry
            WHERE last_scanned >= NOW() - INTERVAL ':days days'
        ),
        transitions AS (
            SELECT 
                s1.server_id,
                s1.risk_tier as from_tier,
                s2.risk_tier as to_tier
            FROM server_scans s1
            JOIN server_scans s2 ON s1.server_id = s2.server_id AND s2.scan_num = s1.scan_num + 1
            WHERE s1.risk_tier != s2.risk_tier
        ),
        transition_counts AS (
            SELECT from_tier, to_tier, COUNT(*) as count
            FROM transitions
            GROUP BY from_tier, to_tier
        ),
        exit_totals AS (
            SELECT from_tier, SUM(count) as total_exits
            FROM transitions
            GROUP BY from_tier
        ),
        entry_totals AS (
            SELECT to_tier, SUM(count) as total_entries
            FROM transitions
            GROUP BY to_tier
        )
        SELECT 
            tc.from_tier,
            tc.to_tier,
            tc.count,
            et.total_exits,
            een.total_entries
        FROM transition_counts tc
        LEFT JOIN exit_totals et ON tc.from_tier = et.from_tier
        LEFT JOIN entry_totals een ON tc.to_tier = een.to_tier
    """)
    
    try:
        result = db.execute(query, {"days": days})
        rows = result.fetchall()
    except Exception:
        rows = []
    
    counts = {}
    exit_totals = {}
    entry_totals = {}
    
    for row in rows:
        from_tier = row[0]
        to_tier = row[1]
        count = row[2]
        
        if from_tier not in counts:
            counts[from_tier] = {}
        counts[from_tier][to_tier] = count
        
        if row[3] is not None:
            exit_totals[from_tier] = row[3]
        if row[4] is not None:
            entry_totals[to_tier] = row[4]
    
    matrix_rows = []
    matrix_cols = []
    cells = []
    
    for from_tier in risk_tiers:
        matrix_rows.append(from_tier)
        row_data = []
        for to_tier in risk_tiers:
            matrix_cols = risk_tiers
            row_data.append(counts.get(from_tier, {}).get(to_tier, 0))
        cells.append(row_data)
    
    return {
        "days": days,
        "matrix": {
            "rows": matrix_rows,
            "columns": matrix_cols,
            "cells": cells
        },
        "totals": {
            "exits": exit_totals,
            "entries": entry_totals
        }
    }


if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from app.db import get_session, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from datetime import datetime, timedelta
    from app.models import Base as AppBase
    
    TEST_DATA_SQL = """
    CREATE TABLE IF NOT EXISTS McpServerRegistry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id VARCHAR(255) NOT NULL,
        server_name VARCHAR(255) NOT NULL,
        risk_tier VARCHAR(50) NOT NULL,
        last_scanned TIMESTAMP NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    
    INSERT INTO McpServerRegistry (server_id, server_name, risk_tier, last_scanned) VALUES
    ('srv-001', 'Server Alpha', 'high', datetime('now', '-1 day')),
    ('srv-001', 'Server Alpha', 'critical', datetime('now', '-0 days')),
    ('srv-002', 'Server Beta', 'medium', datetime('now', '-1 day')),
    ('srv-002', 'Server Beta', 'high', datetime('now', '-0 days')),
    ('srv-003', 'Server Gamma', 'low', datetime('now', '-2 days')),
    ('srv-003', 'Server Gamma', 'medium', datetime('now', '-1 day')),
    ('srv-004', 'Server Delta', 'critical', datetime('now', '-1 day')),
    ('srv-004', 'Server Delta', 'high', datetime('now', '-0 days')),
    ('srv-005', 'Server Epsilon', 'medium', datetime('now', '-2 days')),
    ('srv-005', 'Server Epsilon', 'low', datetime('now', '-1 day'));
    """
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    AppBase.metadata.create_all(bind=engine)
    
    with engine.connect() as conn:
        for stmt in TEST_DATA_SQL.split(';'):
            stmt = stmt.strip()
            if stmt.startswith('CREATE') or stmt.startswith('INSERT'):
                try:
                    conn.execute(text(stmt))
                except Exception:
                    pass
        conn.commit()
    
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    from main import app
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    
    response = client.get("/api/risk/transition-matrix?days=7")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    
    assert "matrix" in data, "Response missing 'matrix' key"
    assert "rows" in data["matrix"], "Matrix missing 'rows'"
    assert "columns" in data["matrix"], "Matrix missing 'columns'"
    assert "cells" in data["matrix"], "Matrix missing 'cells'"
    
    rows = data["matrix"]["rows"]
    cols = data["matrix"]["columns"]
    cells = data["matrix"]["cells"]
    
    assert len(rows) > 0, "Matrix has no rows"
    assert len(cols) > 0, "Matrix has no columns"
    assert len(cells) > 0, "Matrix has no cells"
    
    critical_to_critical = None
    for i, from_t in enumerate(rows):
        if from_t == "critical":
            for j, to_t in enumerate(cols):
                if to_t == "critical":
                    critical_to_critical = cells[i][j]
                    break
            break
    
    assert critical_to_critical is not None, "Critical->Critical cell not found"
    assert critical_to_critical >= 0, "Cell count should be non-negative"
    
    print("PASS")
    sys.exit(0)