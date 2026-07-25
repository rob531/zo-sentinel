from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from datetime import datetime
import csv
import io
from app.db import get_session
from app.models import ServerRegistry, LlmAxisScores, VulnAdvisories
from typing import Optional

router = APIRouter()

def get_csv_response(servers):
    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow([
        "server_id", "name", "registry_source", "url", "trust_score",
        "verdict", "verdict_reasoning", "confidence", "risk_tier",
        "overall_p_top", "overall_p_critical", "cve_count",
        "last_scanned", "scan_count", "first_seen", "last_seen"
    ])

    # Write rows
    for server in servers:
        writer.writerow([
            server.server_id,
            server.name,
            server.registry_source,
            server.url,
            server.trust_score,
            server.verdict,
            server.verdict_reasoning,
            server.confidence,
            server.risk_tier,
            server.overall_p_top,
            server.overall_p_critical,
            server.cve_count,
            server.last_scanned,
            server.scan_count,
            server.first_seen,
            server.last_seen
        ])

    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=registry_export_{datetime.now().strftime('%Y%m%d')}.csv"
        }
    )

@router.get("/servers/export")
async def export_servers(
    risk_tier: Optional[str] = None,
    verdict: Optional[str] = None,
    registry_source: Optional[str] = None,
    limit: int = Query(10000, le=50000),
    db: Session = Depends(get_session)
):
    query = db.query(
        ServerRegistry.server_id,
        ServerRegistry.name,
        ServerRegistry.registry_source,
        ServerRegistry.url,
        ServerRegistry.trust_score,
        ServerRegistry.verdict,
        ServerRegistry.verdict_reasoning,
        ServerRegistry.confidence,
        ServerRegistry.risk_tier,
        LlmAxisScores.p_top.label("overall_p_top"),
        LlmAxisScores.p_critical.label("overall_p_critical"),
        func.count(VulnAdvisories.cve_id).label("cve_count"),
        ServerRegistry.last_scanned,
        ServerRegistry.scan_count,
        ServerRegistry.first_seen,
        ServerRegistry.last_seen
    ).join(
        LlmAxisScores,
        ServerRegistry.server_id == LlmAxisScores.server_id,
        isouter=True
    ).join(
        VulnAdvisories,
        ServerRegistry.server_id == VulnAdvisories.server_id,
        isouter=True
    ).filter(
        LlmAxisScores.axis_name == 'overall_risk'
    ).group_by(
        ServerRegistry.server_id,
        ServerRegistry.name,
        ServerRegistry.registry_source,
        ServerRegistry.url,
        ServerRegistry.trust_score,
        ServerRegistry.verdict,
        ServerRegistry.verdict_reasoning,
        ServerRegistry.confidence,
        ServerRegistry.risk_tier,
        LlmAxisScores.p_top,
        LlmAxisScores.p_critical,
        ServerRegistry.last_scanned,
        ServerRegistry.scan_count,
        ServerRegistry.first_seen,
        ServerRegistry.last_seen
    )

    if risk_tier:
        query = query.filter(ServerRegistry.risk_tier == risk_tier)
    if verdict:
        query = query.filter(ServerRegistry.verdict == verdict)
    if registry_source:
        query = query.filter(ServerRegistry.registry_source == registry_source)

    query = query.limit(limit)
    servers = query.all()

    return get_csv_response(servers)

if __name__ == '__main__':
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, func
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test data
    db = SessionLocal()
    test_servers = [
        ServerRegistry(
            server_id=1,
            name="Test Server 1",
            registry_source="test",
            url="http://test1.com",
            trust_score=0.9,
            verdict="safe",
            verdict_reasoning="Test reasoning",
            confidence=0.95,
            risk_tier="low",
            last_scanned=datetime.now(),
            scan_count=1,
            first_seen=datetime.now(),
            last_seen=datetime.now()
        ),
        ServerRegistry(
            server_id=2,
            name="Test Server 2",
            registry_source="test",
            url="http://test2.com",
            trust_score=0.7,
            verdict="suspicious",
            verdict_reasoning="Test reasoning",
            confidence=0.85,
            risk_tier="medium",
            last_scanned=datetime.now(),
            scan_count=2,
            first_seen=datetime.now(),
            last_seen=datetime.now()
        ),
        ServerRegistry(
            server_id=3,
            name="Test Server 3",
            registry_source="test",
            url="http://test3.com",
            trust_score=0.5,
            verdict="malicious",
            verdict_reasoning="Test reasoning",
            confidence=0.75,
            risk_tier="high",
            last_scanned=datetime.now(),
            scan_count=3,
            first_seen=datetime.now(),
            last_seen=datetime.now()
        )
    ]
    db.add_all(test_servers)
    db.commit()

    # Add test LLM scores
    test_scores = [
        LlmAxisScores(
            server_id=1,
            axis_name="overall_risk",
            p_top=0.8,
            p_critical=0.1
        ),
        LlmAxisScores(
            server_id=2,
            axis_name="overall_risk",
            p_top=0.6,
            p_critical=0.3
        ),
        LlmAxisScores(
            server_id=3,
            axis_name="overall_risk",
            p_top=0.4,
            p_critical=0.5
        )
    ]
    db.add_all(test_scores)
    db.commit()

    # Create test app and client
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/servers/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv"
    assert response.headers["content-disposition"].startswith("attachment; filename=registry_export_")
    assert len(response.content.decode().split('\n')) == 4  # 3 rows + header

    print("PASS")