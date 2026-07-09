from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import mcp_server_registry, mcp_llm_axis_scores, mcp_score_disputes, orgs, users
import requests

router = APIRouter()

@router.get("/investigate/mcp_server_axis_probabilities_analysis_view")
async def investigate_mcp_server_axis_probabilities_analysis_view(session: Session = Depends(get_session)):
    try:
        # Query the authoritative app tables for the quarantined file
        quarantined_file = session.query(mcp_server_registry).filter_by(file_name="mcp_server_axis_probabilities_analysis_view.py").first()
        if not quarantined_file:
            raise HTTPException(status_code=404, detail="Quarantined file not found")

        # Query the mesh_memory table for the investigation details
        response = requests.post("http://127.0.0.1:8772/query", json={
            "table": "mesh_memory",
            "query": {"file_name": "mcp_server_axis_probabilities_analysis_view.py"}
        })
        response.raise_for_status()
        investigation_details = response.json()

        # Query the mcp_score_disputes table for the disputes related to the quarantined file
        disputes = session.query(mcp_score_disputes).filter_by(file_name="mcp_server_axis_probabilities_analysis_view.py").all()

        # Query the orgs and users tables for additional context
        org = session.query(orgs).filter_by(id=quarantined_file.org_id).first()
        user = session.query(users).filter_by(id=quarantined_file.user_id).first()

        # Compile the investigation report
        investigation_report = {
            "quarantined_file": quarantined_file.to_dict(),
            "investigation_details": investigation_details,
            "disputes": [dispute.to_dict() for dispute in disputes],
            "org": org.to_dict() if org else None,
            "user": user.to_dict() if user else None
        }

        return investigation_report
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create a throwaway SQLite session for the self-test
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Insert test data
    session.add(mcp_server_registry(file_name="mcp_server_axis_probabilities_analysis_view.py", org_id=1, user_id=1))
    session.add(mcp_score_disputes(file_name="mcp_server_axis_probabilities_analysis_view.py", dispute_reason="3 consecutive failures in cohort_14_n1"))
    session.add(orgs(id=1, name="Test Org"))
    session.add(users(id=1, name="Test User"))
    session.commit()

    # Override the get_session dependency for the self-test
    from app.dependency_overrides import get_session
    app.dependency_overrides[get_session] = lambda: session

    # Run the self-test
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    response = client.get("/investigate/mcp_server_axis_probabilities_analysis_view")
    assert response.status_code == 200
    print("PASS")