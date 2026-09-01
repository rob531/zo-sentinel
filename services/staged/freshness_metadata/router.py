"""
Freshness metadata service - provides server freshness information.
"""
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["freshness_metadata"])


class AxisFreshness(BaseModel):
    axis_name: str
    scored_at: Optional[datetime]
    freshness_minutes: int
    freshness_tier: str


class FreshnessMetadata(BaseModel):
    server_id: str
    axes: List[AxisFreshness]
    overall_freshness_minutes: int
    overall_freshness_tier: str
    last_scanned: Optional[datetime]
    scan_count: int


def compute_freshness_tier(minutes: int) -> str:
    if minutes < 0:
        return "NEVER"
    elif minutes < 60:
        return "FRESH"
    elif minutes < 1440:
        return "STALE"
    else:
        return "COLD"


def compute_freshness_minutes(scored_at: Optional[datetime], now: datetime) -> int:
    if scored_at is None:
        return -1
    delta = now - scored_at
    return int(delta.total_seconds() / 60)


def get_server_freshness(
    server_id: str,
    db: Session
) -> FreshnessMetadata:
    now = datetime.now(timezone.utc)
    
    # Get server registry info
    registry_query = text("""
        SELECT last_scanned, scan_count, last_assessed
        FROM McpServerRegistry
        WHERE server_id = :server_id
    """)
    registry_result = db.execute(registry_query, {"server_id": server_id}).fetchone()
    
    if registry_result:
        last_scanned = registry_result[0]
        scan_count = registry_result[1]
    else:
        last_scanned = None
        scan_count = 0
    
    # Get axis scores
    scores_query = text("""
        SELECT axis_name, scored_at
        FROM McpLlmAxisScore
        WHERE server_id = :server_id
        ORDER BY scored_at DESC
    """)
    scores_results = db.execute(scores_query, {"server_id": server_id}).fetchall()
    
    # Deduplicate axes, keeping most recent score per axis
    seen_axes = {}
    for row in scores_results:
        axis_name = row[0]
        scored_at = row[1]
        if axis_name not in seen_axes:
            seen_axes[axis_name] = scored_at
    
    # Compute per-axis freshness
    axes = []
    freshness_minutes_list = []
    
    for axis_name, scored_at in seen_axes.items():
        freshness_minutes = compute_freshness_minutes(scored_at, now)
        freshness_tier = compute_freshness_tier(freshness_minutes)
        axes.append(AxisFreshness(
            axis_name=axis_name,
            scored_at=scored_at,
            freshness_minutes=freshness_minutes,
            freshness_tier=freshness_tier
        ))
        if freshness_minutes >= 0:
            freshness_minutes_list.append(freshness_minutes)
    
    # Compute overall freshness
    if last_scanned:
        overall_freshness_minutes = compute_freshness_minutes(last_scanned, now)
    elif freshness_minutes_list:
        overall_freshness_minutes = min(freshness_minutes_list)
    else:
        overall_freshness_minutes = -1
    
    overall_freshness_tier = compute_freshness_tier(overall_freshness_minutes)
    
    return FreshnessMetadata(
        server_id=server_id,
        axes=axes,
        overall_freshness_minutes=overall_freshness_minutes,
        overall_freshness_tier=overall_freshness_tier,
        last_scanned=last_scanned,
        scan_count=scan_count
    )


@router.get("/servers/{server_id}/freshness", response_model=FreshnessMetadata)
def get_freshness(
    server_id: str,
    db: Session = Depends(get_session)
) -> FreshnessMetadata:
    return get_server_freshness(server_id, db)


if __name__ == "__main__":
    import sqlite3
    from unittest.mock import patch, MagicMock
    from fastapi.testclient import TestClient
    from main import app
    
    # Create in-memory SQLite database
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE McpServerRegistry (
            server_id TEXT PRIMARY KEY,
            last_scanned TIMESTAMP,
            scan_count INTEGER,
            last_assessed TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE McpLlmAxisScore (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT,
            axis_name TEXT,
            scored_at TIMESTAMP,
            score_value REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Seed server 1: recent scores (5 minutes ago)
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    recent_time = now - timedelta(minutes=5)
    stale_time = now - timedelta(hours=3)
    
    conn.execute(
        "INSERT INTO McpServerRegistry (server_id, last_scanned, scan_count) VALUES (?, ?, ?)",
        ("server-001", recent_time.isoformat(), 10)
    )
    conn.execute(
        "INSERT INTO McpLlmAxisScore (server_id, axis_name, scored_at) VALUES (?, ?, ?)",
        ("server-001", "security", recent_time.isoformat())
    )
    conn.execute(
        "INSERT INTO McpLlmAxisScore (server_id, axis_name, scored_at) VALUES (?, ?, ?)",
        ("server-001", "compliance", recent_time.isoformat())
    )
    
    # Seed server 2: stale scores (3 hours ago)
    conn.execute(
        "INSERT INTO McpServerRegistry (server_id, last_scanned, scan_count) VALUES (?, ?, ?)",
        ("server-002", stale_time.isoformat(), 5)
    )
    conn.execute(
        "INSERT INTO McpLlmAxisScore (server_id, axis_name, scored_at) VALUES (?, ?, ?)",
        ("server-002", "security", stale_time.isoformat())
    )
    conn.commit()
    
    # Create SQLAlchemy session from SQLite connection
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    sqlite_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    
    # Copy data to SQLAlchemy-managed SQLite
    sa_conn = sqlite_engine.connect()
    sa_conn.execute(text("""
        CREATE TABLE McpServerRegistry (
            server_id TEXT PRIMARY KEY,
            last_scanned TIMESTAMP,
            scan_count INTEGER,
            last_assessed TIMESTAMP
        )
    """))
    sa_conn.execute(text("""
        CREATE TABLE McpLlmAxisScore (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT,
            axis_name TEXT,
            scored_at TIMESTAMP,
            score_value REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    
    # Copy data
    for row in conn.execute("SELECT * FROM McpServerRegistry"):
        sa_conn.execute(
            text("INSERT INTO McpServerRegistry (server_id, last_scanned, scan_count) VALUES (:s, :l, :c)"),
            {"s": row[0], "l": row[1], "c": row[2]}
        )
    for row in conn.execute("SELECT * FROM McpLlmAxisScore"):
        sa_conn.execute(
            text("INSERT INTO McpLlmAxisScore (server_id, axis_name, scored_at) VALUES (:s, :a, :t)"),
            {"s": row[1], "a": row[2], "t": row[3]}
        )
    sa_conn.commit()
    
    TestSession = sessionmaker(bind=sqlite_engine)
    test_session = TestSession()
    
    def override_get_session():
        return test_session
    
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    
    # Test server 1 (FRESH)
    response1 = client.get("/api/servers/server-001/freshness")
    assert response1.status_code == 200, f"Expected 200, got {response1.status_code}"
    data1 = response1.json()
    assert data1["overall_freshness_tier"] == "FRESH", f"Expected FRESH for server-001, got {data1['overall_freshness_tier']}"
    assert data1["server_id"] == "server-001"
    
    # Test server 2 (STALE - 3 hours old)
    response2 = client.get("/api/servers/server-002/freshness")
    assert response2.status_code == 200, f"Expected 200, got {response2.status_code}"
    data2 = response2.json()
    assert data2["overall_freshness_tier"] == "STALE", f"Expected STALE for server-002, got {data2['overall_freshness_tier']}"
    assert data2["server_id"] == "server-002"
    
    conn.close()
    print("PASS")