from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import json

from app.db import get_session
from sqlalchemy.orm import Session
from sqlalchemy import (
    Table, Column, String, Float, Integer, DateTime, Boolean, JSON, 
    MetaData, text, create_engine
)

router = APIRouter(prefix="/api", tags=["entity-detail"])


class AxisScore(BaseModel):
    axis_name: str
    label: str
    label_index: int
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: bool = False


class EntityDetailResponse(BaseModel):
    server_id: str
    name: str
    url: Optional[str] = None
    description: Optional[str] = None
    trust_score: Optional[float] = None
    verdict: Optional[str] = None
    confidence: Optional[float] = None
    risk_tier: Optional[str] = None
    last_scanned: Optional[datetime] = None
    scan_count: Optional[int] = None
    meta: Optional[dict] = None
    axes: List[AxisScore]
    open_disputes: int
    created_at: Optional[datetime] = None
    first_seen: Optional[datetime] = None

    class Config:
        from_attributes = True


@router.get("/servers/{server_id}/detail", response_model=EntityDetailResponse)
async def get_server_detail(
    server_id: str,
    session: Session = Depends(get_session)
) -> EntityDetailResponse:
    """Get detailed entity information including axis scores and disputes."""
    
    # Query server registry
    server_query = text("""
        SELECT 
            server_id, name, url, description,
            trust_score, verdict, confidence, risk_tier,
            last_scanned, scan_count, meta,
            created_at, first_seen
        FROM mcp_server_registry
        WHERE server_id = :server_id
    """)
    
    server_result = session.execute(server_query, {"server_id": server_id}).fetchone()
    
    if not server_result:
        raise HTTPException(status_code=404, detail="Server not found")
    
    # Parse meta JSON if it's a string
    meta = server_result.meta
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = None
    
    # Query axis scores
    axes_query = text("""
        SELECT 
            axis_name, label, label_index,
            p_top, p_critical, p_danger, escalated
        FROM mcp_llm_axis_scores
        WHERE server_id = :server_id
        ORDER BY label_index
    """)
    
    axes_results = session.execute(axes_query, {"server_id": server_id}).fetchall()
    axes = [
        AxisScore(
            axis_name=row.axis_name,
            label=row.label,
            label_index=row.label_index,
            p_top=row.p_top,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
            escalated=bool(row.escalated)
        )
        for row in axes_results
    ]
    
    # Query open disputes count
    disputes_query = text("""
        SELECT COUNT(*) as open_disputes
        FROM mcp_score_disputes
        WHERE server_id = :server_id
        AND status = 'open'
    """)
    
    disputes_result = session.execute(disputes_query, {"server_id": server_id}).fetchone()
    open_disputes = disputes_result[0] if disputes_result else 0
    
    return EntityDetailResponse(
        server_id=server_result.server_id,
        name=server_result.name,
        url=server_result.url,
        description=server_result.description,
        trust_score=server_result.trust_score,
        verdict=server_result.verdict,
        confidence=server_result.confidence,
        risk_tier=server_result.risk_tier,
        last_scanned=server_result.last_scanned,
        scan_count=server_result.scan_count,
        meta=meta,
        axes=axes,
        open_disputes=open_disputes,
        created_at=server_result.created_at,
        first_seen=server_result.first_seen
    )


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy.pool import StaticPool
    
    # Create in-memory SQLite for self-test
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    # Create tables
    metadata = MetaData()
    
    McpServerRegistry = Table(
        'mcp_server_registry', metadata,
        Column('server_id', String, primary_key=True),
        Column('name', String),
        Column('url', String),
        Column('description', String),
        Column('trust_score', Float),
        Column('verdict', String),
        Column('confidence', Float),
        Column('risk_tier', String),
        Column('last_scanned', DateTime),
        Column('scan_count', Integer),
        Column('meta', JSON),
        Column('created_at', DateTime),
        Column('first_seen', DateTime),
    )
    
    McpLlmAxisScore = Table(
        'mcp_llm_axis_scores', metadata,
        Column('id', Integer, primary_key=True, autoincrement=True),
        Column('server_id', String),
        Column('axis_name', String),
        Column('label', String),
        Column('label_index', Integer),
        Column('p_top', Float),
        Column('p_critical', Float),
        Column('p_danger', Float),
        Column('escalated', Boolean),
    )
    
    McpScoreDispute = Table(
        'mcp_score_disputes', metadata,
        Column('id', Integer, primary_key=True, autoincrement=True),
        Column('server_id', String),
        Column('status', String),
        Column('created_at', DateTime),
    )
    
    metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    
    def override_get_session() -> Session:
        session = TestSession()
        try:
            yield session
        finally:
            session.close()
    
    # Seed test data
    session = TestSession()
    
    now = datetime.utcnow()
    
    # Seed servers
    servers = [
        {
            'server_id': 'srv1',
            'name': 'Test Server One',
            'url': 'https://srv1.example.com',
            'description': 'A test server for unit testing',
            'trust_score': 0.85,
            'verdict': 'trusted',
            'confidence': 0.92,
            'risk_tier': 'low',
            'last_scanned': now,
            'scan_count': 10,
            'meta': {'version': '1.0.0'},
            'created_at': now,
            'first_seen': now,
        },
        {
            'server_id': 'srv2',
            'name': 'Test Server Two',
            'url': 'https://srv2.example.com',
            'description': 'Another test server',
            'trust_score': 0.65,
            'verdict': 'unknown',
            'confidence': 0.78,
            'risk_tier': 'medium',
            'last_scanned': now,
            'scan_count': 5,
            'meta': {'version': '2.0.0'},
            'created_at': now,
            'first_seen': now,
        },
        {
            'server_id': 'srv3',
            'name': 'Test Server Three',
            'url': 'https://srv3.example.com',
            'description': 'Third test server',
            'trust_score': 0.45,
            'verdict': 'suspicious',
            'confidence': 0.60,
            'risk_tier': 'high',
            'last_scanned': now,
            'scan_count': 2,
            'meta': {'version': '0.5.0'},
            'created_at': now,
            'first_seen': now,
        },
    ]
    
    for server in servers:
        session.execute(McpServerRegistry.insert().values(**server))
    
    # Seed axis scores for srv1 (all 7 axes)
    axis_names = [
        'overall_risk', 'auth_strength', 'capability_breadth', 
        'data_sensitivity', 'network_egress', 'maintainer_trust', 'exploit_surface'
    ]
    axis_labels = [
        'Overall Risk', 'Auth Strength', 'Capability Breadth',
        'Data Sensitivity', 'Network Egress', 'Maintainer Trust', 'Exploit Surface'
    ]
    
    for i, (axis_name, label) in enumerate(zip(axis_names, axis_labels)):
        session.execute(McpLlmAxisScore.insert().values(
            server_id='srv1',
            axis_name=axis_name,
            label=label,
            label_index=i,
            p_top=0.7 + (i * 0.03),
            p_critical=0.1 + (i * 0.02),
            p_danger=0.15 + (i * 0.01),
            escalated=i < 2  # First two are escalated
        ))
    
    # Seed axis scores for srv2 (partial)
    for i, (axis_name, label) in enumerate(zip(axis_names[:4], axis_labels[:4])):
        session.execute(McpLlmAxisScore.insert().values(
            server_id='srv2',
            axis_name=axis_name,
            label=label,
            label_index=i,
            p_top=0.5 + (i * 0.05),
            p_critical=0.2 + (i * 0.03),
            p_danger=0.2 + (i * 0.02),
            escalated=False
        ))
    
    # Seed disputes for srv1 (2 open, 1 resolved)
    session.execute(McpScoreDispute.insert().values(
        server_id='srv1',
        status='open',
        created_at=now
    ))
    session.execute(McpScoreDispute.insert().values(
        server_id='srv1',
        status='open',
        created_at=now
    ))
    session.execute(McpScoreDispute.insert().values(
        server_id='srv1',
        status='resolved',
        created_at=now
    ))
    
    # Seed disputes for srv2 (1 open)
    session.execute(McpScoreDispute.insert().values(
        server_id='srv2',
        status='open',
        created_at=now
    ))
    
    session.commit()
    session.close()
    
    # Create test app
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(test_app)
    
    # Run acceptance tests
    print("Running self-test for mcp_entity_detail_api...")
    
    # Test 1: GET /api/servers/srv1/detail should return 200
    response = client.get("/api/servers/srv1/detail")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    print("✓ GET /api/servers/srv1/detail returns 200")
    
    data = response.json()
    
    # Test 2: Assert server name
    assert data['name'] == 'Test Server One', f"Expected 'Test Server One', got {data['name']}"
    print("✓ Server name is correct")
    
    # Test 3: Assert all 7 axes present
    assert len(data['axes']) == 7, f"Expected 7 axes, got {len(data['axes'])}"
    axis_names_found = {ax['axis_name'] for ax in data['axes']}
    expected_axes = set(axis_names)
    assert axis_names_found == expected_axes, f"Expected axes {expected_axes}, got {axis_names_found}"
    print("✓ All 7 axes present")
    
    # Test 4: Assert open_disputes count
    assert data['open_disputes'] == 2, f"Expected 2 open disputes, got {data['open_disputes']}"
    print("✓ Open disputes count is correct")
    
    # Test 5: Verify response structure
    assert 'server_id' in data
    assert 'url' in data
    assert 'trust_score' in data
    assert 'verdict' in data
    assert 'risk_tier' in data
    assert 'axes' in data
    assert 'meta' in data
    print("✓ Response structure is complete")
    
    # Test 6: Verify axis structure
    for ax in data['axes']:
        assert 'axis_name' in ax
        assert 'label' in ax
        assert 'label_index' in ax
        assert 'p_top' in ax
        assert 'p_critical' in ax
        assert 'p_danger' in ax
        assert 'escalated' in ax
    print("✓ Axis structure is complete")
    
    # Test 7: Test non-existent server returns 404
    response = client.get("/api/servers/nonexistent/detail")
    assert response.status_code == 404
    print("✓ Non-existent server returns 404")
    
    print("\n" + "=" * 40)
    print("PASS")
    print("=" * 40)