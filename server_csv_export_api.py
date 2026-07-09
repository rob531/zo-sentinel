from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session
from io import StringIO
import csv

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()

def generate_csv(servers):
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["server_id", "name", "risk_tier", "trust_score", "verdict", "last_assessed"])
    for server in servers:
        writer.writerow([
            server.server_id,
            server.name,
            server.risk_tier,
            server.trust_score,
            server.verdict,
            server.last_assessed
        ])
    return output.getvalue()

@router.get("/servers/export")
async def export_servers_csv(
    risk_tier: str = None,
    min_trust_score: float = None,
    limit: int = Query(500, le=5000),
    db: Session = Depends(get_session)
):
    query = db.query(McpServerRegistry)

    if risk_tier:
        query = query.filter(McpServerRegistry.risk_tier == risk_tier)
    if min_trust_score is not None:
        query = query.filter(McpServerRegistry.trust_score >= min_trust_score)

    servers = query.limit(limit).all()

    csv_content = generate_csv(servers)

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=servers_export.csv"
        }
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpServerRegistry
    from sqlalchemy.orm import sessionmaker

    # Test setup
    TestSession = sessionmaker(bind=engine)
    test_db = TestSession()

    # Override dependency for testing
    from app import dependency_overrides
    dependency_overrides[get_session] = lambda: test_db

    # Create test data
    test_servers = [
        McpServerRegistry(
            server_id="1",
            name="Test Server 1",
            risk_tier="high",
            trust_score=0.8,
            verdict="safe",
            last_assessed="2023-01-01"
        ),
        McpServerRegistry(
            server_id="2",
            name="Test Server 2",
            risk_tier="medium",
            trust_score=0.6,
            verdict="safe",
            last_assessed="2023-01-02"
        ),
        McpServerRegistry(
            server_id="3",
            name="Test Server 3",
            risk_tier="high",
            trust_score=0.9,
            verdict="safe",
            last_assessed="2023-01-03"
        )
    ]
    test_db.add_all(test_servers)
    test_db.commit()

    # Create test client
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Test 1: Check response is bytes and has Content-Disposition
    response = client.get("/servers/export")
    assert isinstance(response.content, bytes)
    assert "Content-Disposition" in response.headers
    assert "attachment" in response.headers["Content-Disposition"]

    # Test 2: Check filtering by risk_tier
    response = client.get("/servers/export?risk_tier=high")
    csv_data = response.content.decode("utf-8")
    rows = csv_data.split("\n")
    assert len(rows) == 3  # Header + 2 high risk servers

    print("PASS")