from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api")

def get_freshness_metrics(
    session: Session,
    hours: int = 24
) -> Dict:
    """Compute server freshness metrics from the database."""
    # Calculate the threshold for SLA breach
    sla_threshold = timedelta(hours=hours).total_seconds()
    current_time = datetime.utcnow()

    # Query servers and their last scan/assessment times
    servers = session.query(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.risk_tier,
        McpServerRegistry.last_scanned,
        McpServerRegistry.last_assessed
    ).all()

    results = []
    breached_count = 0

    for server in servers:
        server_id = server.server_id
        name = server.name
        risk_tier = server.risk_tier
        last_scanned = server.last_scanned
        last_assessed = server.last_assessed

        # Calculate time since last scan and assessment
        time_since_scan = (current_time - last_scanned).total_seconds() if last_scanned else None
        time_since_assessed = (current_time - last_assessed).total_seconds() if last_assessed else None

        # Convert to hours
        hours_since_scan = time_since_scan / 3600 if time_since_scan is not None else None
        hours_since_assessed = time_since_assessed / 3600 if time_since_assessed is not None else None

        # Check SLA breach
        sla_breached = False
        breach_minutes = 0

        if time_since_scan is not None and time_since_scan > sla_threshold:
            sla_breached = True
            breach_minutes = (time_since_scan - sla_threshold) / 60
            breached_count += 1

        results.append({
            "server_id": server_id,
            "name": name,
            "risk_tier": risk_tier,
            "last_scanned": last_scanned.isoformat() if last_scanned else None,
            "last_assessed": last_assessed.isoformat() if last_assessed else None,
            "hours_since_scan": hours_since_scan,
            "hours_since_assessed": hours_since_assessed,
            "sla_breached": sla_breached,
            "breach_minutes": breach_minutes
        })

    return {
        "servers": results,
        "summary": {
            "total": len(servers),
            "breached": breached_count,
            "healthy": len(servers) - breached_count
        }
    }

@router.get("/servers/freshness")
async def get_server_freshness(
    hours: int = 24,
    session: Session = Depends(get_session)
) -> Dict:
    """Endpoint to get server freshness metrics."""
    try:
        return get_freshness_metrics(session, hours)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Create test data
    from datetime import datetime, timedelta

    test_session = SessionLocal()
    test_session.execute(
        McpServerRegistry.__table__.insert(),
        [
            {
                "server_id": "server1",
                "name": "Test Server 1",
                "risk_tier": "high",
                "last_scanned": datetime.utcnow() - timedelta(hours=25),
                "last_assessed": datetime.utcnow() - timedelta(hours=23)
            },
            {
                "server_id": "server2",
                "name": "Test Server 2",
                "risk_tier": "medium",
                "last_scanned": datetime.utcnow() - timedelta(hours=23),
                "last_assessed": datetime.utcnow() - timedelta(hours=22)
            },
            {
                "server_id": "server3",
                "name": "Test Server 3",
                "risk_tier": "low",
                "last_scanned": datetime.utcnow() - timedelta(hours=20),
                "last_assessed": datetime.utcnow() - timedelta(hours=19)
            }
        ]
    )
    test_session.commit()

    # Setup FastAPI for testing
    test_app = FastAPI()
    test_app.include_router(router)

    # Override get_session for testing
    def get_test_session():
        try:
            db = SessionLocal()
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = get_test_session

    # Run test
    from fastapi.testclient import TestClient
    client = TestClient(test_app)

    response = client.get("/api/servers/freshness?hours=24")
    assert response.status_code == 200
    data = response.json()

    assert data["summary"]["breached"] == 1
    assert any(server["name"] == "Test Server 2" and not server["sla_breached"] for server in data["servers"])

    print("PASS")