from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from typing import Optional
import csv
from io import StringIO
from datetime import datetime
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores

router = APIRouter()

def calculate_overall_risk(scores):
    """Calculate overall risk score from axis scores."""
    if not scores:
        return 0.0
    return sum(score.value for score in scores) / len(scores)

def get_risk_tier(overall_risk: float) -> str:
    """Determine risk tier based on overall risk score."""
    if overall_risk >= 0.8:
        return "High"
    elif overall_risk >= 0.5:
        return "Medium"
    else:
        return "Low"

def get_risk_tier_csv(
    min_tier: Optional[str] = None,
    max_tier: Optional[str] = None,
    session: Session = Depends(get_session)
) -> Response:
    """Generate CSV export of server risk tier summaries."""
    # Get all servers with their scores
    servers = session.query(MCPServerRegistry).all()

    # Prepare CSV output
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["server_id", "name", "risk_tier", "overall_risk", "last_assessed"])

    for server in servers:
        # Get all scores for this server
        scores = session.query(MCPLLMAxisScores).filter(
            MCPLLMAxisScores.server_id == server.id
        ).all()

        # Calculate overall risk and tier
        overall_risk = calculate_overall_risk(scores)
        risk_tier = get_risk_tier(overall_risk)

        # Apply tier filters if provided
        if min_tier and risk_tier < min_tier:
            continue
        if max_tier and risk_tier > max_tier:
            continue

        # Write row to CSV
        writer.writerow([
            server.id,
            server.name,
            risk_tier,
            f"{overall_risk:.2f}",
            server.last_assessed.isoformat() if server.last_assessed else ""
        ])

    # Return CSV response
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=server_risk_tiers.csv"}
    )

router.get("/export/risk-tiers", response_class=Response)(get_risk_tier_csv)

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test app and override dependencies
    app = FastAPI()
    app.include_router(router)

    # Create in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override get_session for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Add test data
    test_session = TestSession()
    test_server = MCPServerRegistry(
        id=1,
        name="Test Server",
        last_assessed=datetime.now()
    )
    test_session.add(test_server)
    test_session.add(MCPLLMAxisScores(
        server_id=1,
        axis="test_axis",
        value=0.7
    ))
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/export/risk-tiers")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv"

    # Parse CSV and verify content
    csv_data = response.content.decode("utf-8")
    lines = csv_data.splitlines()
    assert lines[0] == "server_id,name,risk_tier,overall_risk,last_assessed"
    assert len(lines) > 1  # At least one data row

    print("PASS")