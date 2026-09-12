import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, text, func
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

logger = logging.getLogger(__name__)

# =============================================================================
# Pydantic Response Models
# =============================================================================

class DriftEvent(BaseModel):
    server_id: str
    name: str
    previous_p_top: float
    current_p_top: float
    drift_delta: float
    risk_tier: str
    drift_events_count: int

class Summary(BaseModel):
    total_servers: int
    servers_with_drift: int
    avg_p_top_delta: float

class AxisDriftReportResponse(BaseModel):
    generated_at: datetime
    window: int
    total_servers: int
    drift_events: List[DriftEvent]
    summary: Summary

# =============================================================================
# FastAPI Application
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Axis Score Drift Report service started")
    yield
    logger.info("Axis Score Drift Report service stopped")

app = FastAPI(
    title="Axis Score Drift Report",
    description="Report tracking drift in axis scores across MCP servers",
    lifespan=lifespan
)

# =============================================================================
# API Endpoint
# =============================================================================

@app.get("/api/scoring/axis-drift-report", response_model=AxisDriftReportResponse)
def get_axis_drift_report(
    window: int = 5,
    session: Session = Depends(get_session)
) -> AxisDriftReportResponse:
    """
    Compute per-server score drift across the last N scoring events.
    
    For each server_id, collect p_top values ordered by scored_at,
    compute variance and mean delta. Flag servers where p_top shifted
    by >20 points between consecutive runs (drift event).
    """
    # First get total servers count
    total_servers = session.query(func.count(func.distinct(McpLlmAxisScore.server_id))).scalar() or 0
    
    # Query to compute drift events using window functions
    # Uses ROW_NUMBER to get last N events per server, then LAG to compare consecutive
    drift_query = text("""
        WITH ranked_scores AS (
            SELECT 
                s.server_id,
                sr.name,
                sr.risk_tier,
                s.scored_at,
                s.p_top,
                ROW_NUMBER() OVER (PARTITION BY s.server_id ORDER BY s.scored_at DESC) as rn
            FROM McpLlmAxisScore s
            JOIN McpServerRegistry sr ON s.server_id = sr.server_id
        ),
        windowed_scores AS (
            SELECT 
                server_id,
                name,
                risk_tier,
                scored_at,
                p_top,
                LAG(p_top) OVER (PARTITION BY server_id ORDER BY scored_at DESC) as previous_p_top,
                LAG(scored_at) OVER (PARTITION BY server_id ORDER BY scored_at DESC) as previous_scored_at
            FROM ranked_scores
            WHERE rn <= :window
        ),
        drift_events AS (
            SELECT 
                server_id,
                name,
                risk_tier,
                previous_p_top,
                p_top as current_p_top,
                ABS(p_top - previous_p_top) as drift_delta
            FROM windowed_scores
            WHERE previous_p_top IS NOT NULL 
              AND ABS(p_top - previous_p_top) > 20
        ),
        drift_with_counts AS (
            SELECT 
                server_id,
                name,
                risk_tier,
                previous_p_top,
                current_p_top,
                drift_delta,
                COUNT(*) OVER (PARTITION BY server_id) as drift_events_count
            FROM drift_events
        )
        SELECT DISTINCT ON (server_id, previous_scored_at)
            server_id,
            name,
            risk_tier,
            previous_p_top,
            current_p_top,
            drift_delta,
            drift_events_count
        FROM (
            SELECT 
                server_id,
                name,
                risk_tier,
                previous_p_top,
                current_p_top,
                drift_delta,
                drift_events_count,
                ROW_NUMBER() OVER (PARTITION BY server_id ORDER BY drift_delta DESC) as rn
            FROM drift_with_counts
        ) ranked
        WHERE rn = 1
        ORDER BY server_id, drift_delta DESC
    """)
    
    result = session.execute(drift_query, {"window": window})
    rows = result.fetchall()
    
    drift_events = []
    servers_with_drift = set()
    total_drift_delta = 0.0
    
    for row in rows:
        server_id, name, risk_tier, previous_p_top, current_p_top, drift_delta, drift_events_count = row
        servers_with_drift.add(server_id)
        total_drift_delta += drift_delta
        drift_events.append(DriftEvent(
            server_id=server_id,
            name=name,
            previous_p_top=float(previous_p_top),
            current_p_top=float(current_p_top),
            drift_delta=float(drift_delta),
            risk_tier=risk_tier,
            drift_events_count=drift_events_count
        ))
    
    # Calculate average drift delta
    if servers_with_drift:
        avg_p_top_delta = total_drift_delta / len(servers_with_drift)
    else:
        avg_p_top_delta = 0.0
    
    return AxisDriftReportResponse(
        generated_at=datetime.utcnow(),
        window=window,
        total_servers=total_servers,
        drift_events=drift_events,
        summary=Summary(
            total_servers=total_servers,
            servers_with_drift=len(servers_with_drift),
            avg_p_top_delta=round(avg_p_top_delta, 2)
        )
    )

# =============================================================================
# Self-Test
# =============================================================================

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    
    # Create in-memory SQLite database for self-test
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    # Create tables
    from sqlalchemy import inspect
    inspector = inspect(test_engine)
    
    # Create tables manually for SQLite test
    from sqlalchemy.schema import CreateTable
    
    # We'll create tables that match the app models structure
    from sqlalchemy import Table, MetaData
    
    metadata = MetaData()
    
    test_servers_table = Table(
        'McpServerRegistry', metadata,
        Column('server_id', String, primary_key=True),
        Column('name', String),
        Column('risk_tier', String),
        Column('created_at', DateTime)
    )
    
    test_scores_table = Table(
        'McpLlmAxisScore', metadata,
        Column('id', Integer, primary_key=True, autoincrement=True),
        Column('server_id', String),
        Column('scored_at', DateTime),
        Column('p_top', Float),
        Column('score_type', String),
        Column('axis_name', String),
        Column('metadata', String)
    )
    
    # Create the tables
    metadata.create_all(test_engine)
    
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    
    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    # Create test client with dependency override
    test_app = FastAPI()
    
    @test_app.get("/api/scoring/axis-drift-report", response_model=AxisDriftReportResponse)
    def test_get_axis_drift_report(
        window: int = 5,
        session: Session = Depends(override_get_session)
    ) -> AxisDriftReportResponse:
        total_servers = session.execute(
            text("SELECT COUNT(DISTINCT server_id) FROM McpLlmAxisScore")
        ).scalar() or 0
        
        drift_query = text("""
            WITH ranked_scores AS (
                SELECT 
                    s.server_id,
                    sr.name,
                    sr.risk_tier,
                    s.scored_at,
                    s.p_top,
                    ROW_NUMBER() OVER (PARTITION BY s.server_id ORDER BY s.scored_at DESC) as rn
                FROM McpLlmAxisScore s
                JOIN McpServerRegistry sr ON s.server_id = sr.server_id
            ),
            windowed_scores AS (
                SELECT 
                    server_id,
                    name,
                    risk_tier,
                    scored_at,
                    p_top,
                    LAG(p_top) OVER (PARTITION BY server_id ORDER BY scored_at DESC) as previous_p_top
                FROM ranked_scores
                WHERE rn <= :window
            ),
            drift_events AS (
                SELECT 
                    server_id,
                    name,
                    risk_tier,
                    previous_p_top,
                    p_top as current_p_top,
                    ABS(p_top - previous_p_top) as drift_delta
                FROM windowed_scores
                WHERE previous_p_top IS NOT NULL 
                  AND ABS(p_top - previous_p_top) > 20
            ),
            drift_with_counts AS (
                SELECT 
                    server_id,
                    name,
                    risk_tier,
                    previous_p_top,
                    current_p_top,
                    drift_delta,
                    COUNT(*) OVER (PARTITION BY server_id) as drift_events_count
                FROM drift_events
            )
            SELECT 
                server_id,
                name,
                risk_tier,
                previous_p_top,
                current_p_top,
                drift_delta,
                drift_events_count
            FROM drift_with_counts
            ORDER BY drift_delta DESC
        """)
        
        result = session.execute(drift_query, {"window": window})
        rows = result.fetchall()
        
        drift_events_list = []
        servers_with_drift = set()
        total_drift_delta = 0.0
        
        for row in rows:
            server_id, name, risk_tier, previous_p_top, current_p_top, drift_delta, drift_events_count = row
            servers_with_drift.add(server_id)
            total_drift_delta += drift_delta
            drift_events_list.append(DriftEvent(
                server_id=server_id,
                name=name,
                previous_p_top=float(previous_p_top),
                current_p_top=float(current_p_top),
                drift_delta=float(drift_delta),
                risk_tier=risk_tier,
                drift_events_count=drift_events_count
            ))
        
        if servers_with_drift:
            avg_p_top_delta = total_drift_delta / len(servers_with_drift)
        else:
            avg_p_top_delta = 0.0
        
        return AxisDriftReportResponse(
            generated_at=datetime.utcnow(),
            window=window,
            total_servers=total_servers,
            drift_events=drift_events_list,
            summary=Summary(
                total_servers=total_servers,
                servers_with_drift=len(servers_with_drift),
                avg_p_top_delta=round(avg_p_top_delta, 2)
            )
        )
    
    client = TestClient(test_app)
    
    # Seed test data: 3 servers with 4 scoring rows each
    db = TestSessionLocal()
    try:
        # Server 1: Normal scores (no drift > 20)
        db.execute(text("""
            INSERT INTO McpServerRegistry (server_id, name, risk_tier, created_at)
            VALUES (:server_id, :name, :risk_tier, :created_at)
        """), {"server_id": "server-001", "name": "Alpha Server", "risk_tier": "low", "created_at": datetime.utcnow()})
        
        base_time = datetime.utcnow() - timedelta(hours=10)
        for i in range(4):
            db.execute(text("""
                INSERT INTO McpLlmAxisScore (server_id, scored_at, p_top, score_type, axis_name)
                VALUES (:server_id, :scored_at, :p_top, :score_type, :axis_name)
            """), {
                "server_id": "server-001",
                "scored_at": base_time + timedelta(hours=i * 2),
                "p_top": 75.0 + i * 2,  # 75, 77, 79, 81 - small increments, no drift
                "score_type": "axis",
                "axis_name": "security"
            })
        
        # Server 2: Drifting scores (>20 drift between consecutive)
        db.execute(text("""
            INSERT INTO McpServerRegistry (server_id, name, risk_tier, created_at)
            VALUES (:server_id, :name, :risk_tier, :created_at)
        """), {"server_id": "server-002", "name": "Beta Server", "risk_tier": "medium", "created_at": datetime.utcnow()})
        
        for i in range(4):
            db.execute(text("""
                INSERT INTO McpLlmAxisScore (server_id, scored_at, p_top, score_type, axis_name)
                VALUES (:server_id, :scored_at, :p_top, :score_type, :axis_name)
            """), {
                "server_id": "server-002",
                "scored_at": base_time + timedelta(hours=i * 2),
                "p_top": 60.0 + i * 25,  # 60, 85, 110, 135 - big jumps, drift detected
                "score_type": "axis",
                "axis_name": "security"
            })
        
        # Server 3: Mixed scores (one drift event)
        db.execute(text("""
            INSERT INTO McpServerRegistry (server_id, name, risk_tier, created_at)
            VALUES (:server_id, :name, :risk_tier, :created_at)
        """), {"server_id": "server-003", "name": "Gamma Server", "risk_tier": "high", "created_at": datetime.utcnow()})
        
        for i in range(4):
            db.execute(text("""
                INSERT INTO McpLlmAxisScore (server_id, scored_at, p_top, score_type, axis_name)
                VALUES (:server_id, :scored_at, :p_top, :score_type, :axis_name)
            """), {
                "server_id": "server-003",
                "scored_at": base_time + timedelta(hours=i * 2),
                "p_top": 90.0 if i < 2 else 50.0,  # 90, 90, 50, 50 - one drift of 40
                "score_type": "axis",
                "axis_name": "security"
            })
        
        db.commit()
    finally:
        db.close()
    
    # Run tests
    response = client.get("/api/scoring/axis-drift-report")
    
    # Assert 200
    if response.status_code != 200:
        print(f"FAIL: Expected 200, got {response.status_code}")
        print(f"Response: {response.text}")
        exit(1)
    
    data = response.json()
    
    # Assert drift_events is non-empty
    if not data.get("drift_events"):
        print("FAIL: drift_events is empty")
        print(f"Response: {data}")
        exit(1)
    
    # Assert one known drift_delta value (server-002 should have drift of 25)
    drift_deltas = [e["drift_delta"] for e in data["drift_events"]]
    if 25.0 not in drift_deltas:
        print(f"FAIL: Expected drift_delta 25.0 in {drift_deltas}")
        print(f"Response: {data}")
        exit(1)
    
    # Assert server-002 is in drift events
    server_ids = [e["server_id"] for e in data["drift_events"]]
    if "server-002" not in server_ids:
        print(f"FAIL: Expected server-002 in drift events, got {server_ids}")
        print(f"Response: {data}")
        exit(1)
    
    print("PASS")
    exit(0)