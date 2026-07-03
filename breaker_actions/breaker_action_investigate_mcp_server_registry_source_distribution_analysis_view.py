from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Orgs, Users
import requests
from typing import List, Dict, Optional
import logging

app = FastAPI()
logger = logging.getLogger(__name__)

def investigate_mcp_server_registry_source_distribution_analysis_view(
    session: Session = Depends(get_session)
) -> Dict[str, Optional[List[Dict]]]:
    """
    Investigate the MCP server registry source distribution analysis view.
    Returns a dictionary with the analysis results or None if no data is found.
    """
    try:
        # Query MCPServerRegistry for source distribution analysis
        server_registry_data = session.query(MCPServerRegistry).all()

        if not server_registry_data:
            logger.warning("No data found in MCPServerRegistry")
            return {"server_registry_data": None}

        # Query MCPLLMAxisScores for related scores
        llm_scores = session.query(MCPLLMAxisScores).all()

        # Query MCPScoreDisputes for any disputes
        score_disputes = session.query(MCPScoreDisputes).all()

        # Query ZoComputer store for signal scores and mesh memory
        try:
            response = requests.post(
                "http://127.0.0.1:8772/query",
                json={
                    "query": "SELECT * FROM mcp_signal_scores",
                    "query2": "SELECT * FROM mesh_memory"
                }
            )
            response.raise_for_status()
            zo_computer_data = response.json()
        except requests.RequestException as e:
            logger.error(f"Error querying ZoComputer store: {e}")
            zo_computer_data = {"mcp_signal_scores": [], "mesh_memory": []}

        return {
            "server_registry_data": [{"id": sr.id, "source": sr.source} for sr in server_registry_data],
            "llm_scores": [{"id": llm.id, "score": llm.score} for llm in llm_scores],
            "score_disputes": [{"id": dispute.id, "reason": dispute.reason} for dispute in score_disputes],
            "mcp_signal_scores": zo_computer_data.get("mcp_signal_scores", []),
            "mesh_memory": zo_computer_data.get("mesh_memory", [])
        }
    except Exception as e:
        logger.error(f"Error investigating MCP server registry source distribution: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the dependency for self-test
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    # Mock data for self-test
    test_server = MCPServerRegistry(source="test_source")
    test_llm = MCPLLMAxisScores(score=0.8)
    test_dispute = MCPScoreDisputes(reason="test_reason")

    test_session = TestSession()
    test_session.add_all([test_server, test_llm, test_dispute])
    test_session.commit()

    # Run the self-test
    result = investigate_mcp_server_registry_source_distribution_analysis_view()
    if result and all(len(v) > 0 for v in result.values()):
        print("PASS")
    else:
        print("FAIL")