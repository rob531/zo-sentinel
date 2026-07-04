from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Org, User
from typing import List, Dict, Optional
import requests
from datetime import datetime

app = FastAPI()

def investigate_mcp_server_registry_source_distribution(
    session: Session = Depends(get_session)
) -> Dict[str, Optional[List[Dict]]]:
    """
    Investigate the MCP server registry source distribution by analyzing the dashboard view data.
    Returns a dictionary with the investigation results.
    """
    try:
        # Fetch MCP server registry data
        server_registries = session.query(MCPServerRegistry).all()
        server_registry_data = [
            {
                "id": sr.id,
                "source": sr.source,
                "status": sr.status,
                "created_at": sr.created_at,
                "updated_at": sr.updated_at
            }
            for sr in server_registries
        ]

        # Fetch MCP LLM axis scores data
        llm_axis_scores = session.query(MCPLLMAxisScores).all()
        llm_axis_scores_data = [
            {
                "id": las.id,
                "server_id": las.server_id,
                "axis": las.axis,
                "score": las.score,
                "created_at": las.created_at,
                "updated_at": las.updated_at
            }
            for las in llm_axis_scores
        ]

        # Fetch MCP score disputes data
        score_disputes = session.query(MCPScoreDisputes).all()
        score_disputes_data = [
            {
                "id": sd.id,
                "server_id": sd.server_id,
                "axis": sd.axis,
                "dispute_reason": sd.dispute_reason,
                "status": sd.status,
                "created_at": sd.created_at,
                "updated_at": sd.updated_at
            }
            for sd in score_disputes
        ]

        # Fetch data from ZoComputer store via write_service
        write_service_url = "http://127.0.0.1:8772/query"
        mesh_memory_query = {
            "query": "SELECT * FROM mesh_memory WHERE key LIKE 'mcp_server_registry_%'"
        }
        response = requests.post(write_service_url, json=mesh_memory_query)
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Error querying ZoComputer store")

        mesh_memory_data = response.json()

        return {
            "server_registries": server_registry_data,
            "llm_axis_scores": llm_axis_scores_data,
            "score_disputes": score_disputes_data,
            "mesh_memory": mesh_memory_data
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from app.db import Base, engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for self-test
    test_engine = engine
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Mock data for self-test
    test_server_registry = MCPServerRegistry(
        source="test_source",
        status="active",
        created_at=datetime.now(),
        updated_at=datetime.now()
    )
    test_llm_axis_score = MCPLLMAxisScores(
        server_id=1,
        axis="test_axis",
        score=0.9,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )
    test_score_dispute = MCPScoreDisputes(
        server_id=1,
        axis="test_axis",
        dispute_reason="test_reason",
        status="open",
        created_at=datetime.now(),
        updated_at=datetime.now()
    )

    with TestSession() as session:
        session.add(test_server_registry)
        session.add(test_llm_axis_score)
        session.add(test_score_dispute)
        session.commit()

    # Run self-test
    try:
        result = investigate_mcp_server_registry_source_distribution()
        if result:
            print("PASS")
        else:
            print("FAIL")
    except Exception as e:
        print(f"FAIL: {str(e)}")