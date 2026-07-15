from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from datetime import datetime
import csv
from io import StringIO
from pydantic import BaseModel
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores

router = APIRouter()

class HighRiskServerRow(BaseModel):
    server_id: str
    name: str
    url: str
    registry_source: str
    risk_tier: str
    trust_score: float
    overall_axis_p_top: float
    auth_strength: float
    capability_breadth: float
    data_sensitivity: float
    network_egress: float
    maintainer_trust: float
    exploit_surface: float
    last_scanned: datetime
    first_seen: datetime

def generate_csv(rows: list[HighRiskServerRow]) -> str:
    output = StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_NONNUMERIC)

    # Write header
    writer.writerow([
        "server_id", "name", "url", "registry_source", "risk_tier",
        "trust_score", "overall_axis_p_top", "auth_strength",
        "capability_breadth", "data_sensitivity", "network_egress",
        "maintainer_trust", "exploit_surface", "last_scanned", "first_seen"
    ])

    # Write rows
    for row in rows:
        writer.writerow([
            row.server_id, row.name, row.url, row.registry_source, row.risk_tier,
            row.trust_score, row.overall_axis_p_top, row.auth_strength,
            row.capability_breadth, row.data_sensitivity, row.network_egress,
            row.maintainer_trust, row.exploit_surface,
            row.last_scanned.isoformat(), row.first_seen.isoformat()
        ])

    return output.getvalue()

@router.get("/high-risk-servers/export/csv")
async def export_high_risk_servers_csv(db: Session = Depends(get_session)):
    # Query high risk servers
    high_risk_servers = db.query(MCPServerRegistry).filter(
        MCPServerRegistry.risk_tier.in_(["HIGH_RISK_ISOLATED", "KNOWN_THREAT"])
    ).all()

    if not high_risk_servers:
        raise HTTPException(status_code=404, detail="No high risk servers found")

    # Get server IDs for axis scores query
    server_ids = [server.server_id for server in high_risk_servers]

    # Query axis scores
    axis_scores = db.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.server_id.in_(server_ids)
    ).all()

    # Create score mapping for quick lookup
    score_map = {
        score.server_id: {
            "overall_axis_p_top": score.overall_axis_p_top,
            "auth_strength": score.auth_strength,
            "capability_breadth": score.capability_breadth,
            "data_sensitivity": score.data_sensitivity,
            "network_egress": score.network_egress,
            "maintainer_trust": score.maintainer_trust,
            "exploit_surface": score.exploit_surface
        }
        for score in axis_scores
    }

    # Build rows
    rows = []
    for server in high_risk_servers:
        scores = score_map.get(server.server_id, {})
        rows.append(HighRiskServerRow(
            server_id=server.server_id,
            name=server.name,
            url=server.url,
            registry_source=server.registry_source,
            risk_tier=server.risk_tier,
            trust_score=server.trust_score,
            overall_axis_p_top=scores.get("overall_axis_p_top", 0.0),
            auth_strength=scores.get("auth_strength", 0.0),
            capability_breadth=scores.get("capability_breadth", 0.0),
            data_sensitivity=scores.get("data_sensitivity", 0.0),
            network_egress=scores.get("network_egress", 0.0),
            maintainer_trust=scores.get("maintainer_trust", 0.0),
            exploit_surface=scores.get("exploit_surface", 0.0),
            last_scanned=server.last_scanned,
            first_seen=server.first_seen
        ))

    # Generate CSV
    csv_content = generate_csv(rows)

    # Create response
    filename = f"high_risk_servers_{datetime.now().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from datetime import datetime, timedelta

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_server = MCPServerRegistry(
        server_id="test-server-1",
        name="Test Server 1",
        url="https://test-server-1.example.com",
        registry_source="test",
        risk_tier="HIGH_RISK_ISOLATED",
        trust_score=0.2,
        last_scanned=datetime.now(),
        first_seen=datetime.now() - timedelta(days=7)
    )
    test_session.add(test_server)

    test_axis_scores = MCPLLMAxisScores(
        server_id="test-server-1",
        overall_axis_p_top=0.9,
        auth_strength=0.8,
        capability_breadth=0.7,
        data_sensitivity=0.6,
        network_egress=0.5,
        maintainer_trust=0.4,
        exploit_surface=0.3
    )
    test_session.add(test_axis_scores)
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/high-risk-servers/export/csv")

    # Verify response
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "HIGH_RISK_ISOLATED" in response.text

    print("PASS")