from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session
import re

router = APIRouter()

class AskRouteRequest(BaseModel):
    question: str
    server_id_filter: Optional[str] = None

class Snippet(BaseModel):
    server_id: str
    snippet: str
    content_hash: str

class AskRouteResponse(BaseModel):
    intent: str
    confidence: float
    routing_target: str
    snippets: List[Snippet]
    expanded_terms: List[str]

def classify_intent(question: str) -> tuple[str, float]:
    question_lower = question.lower()

    if re.search(r'\bcompare\b.*\bvs\b|\bversus\b', question_lower):
        return "compare_servers", 0.95
    elif re.search(r'\bhigh[- ]?risk\b|\bcritical\b.*\bauth_strength\b', question_lower):
        return "find_critical_axis", 0.9
    elif re.search(r'\bexplain\b.*\bverdict\b', question_lower):
        return "explain_verdict", 0.85
    elif re.search(r'\bsearch\b.*\bregistry\b', question_lower):
        return "search_registry", 0.8
    else:
        return "general_question", 0.7

def expand_terms(question: str) -> List[str]:
    terms = question.split()
    expanded = []
    for term in terms:
        if term.lower() in ["server", "servers"]:
            expanded.extend(["mcp_server", "registry"])
        elif term.lower() in ["risk", "critical"]:
            expanded.extend(["auth_strength", "security"])
        expanded.append(term)
    return expanded

@router.post("/ask/route", response_model=AskRouteResponse)
async def route_question(
    request: AskRouteRequest,
    db: Session = Depends(get_session)
):
    intent, confidence = classify_intent(request.question)
    expanded_terms = expand_terms(request.question)

    routing_map = {
        "compare_servers": "server_comparison",
        "find_critical_axis": "axis_search",
        "explain_verdict": "verdict_explanation",
        "search_registry": "registry_search",
        "general_question": "general_retrieval"
    }
    routing_target = routing_map.get(intent, "general_retrieval")

    snippets = []
    if request.server_id_filter:
        server = db.query(MCPServerRegistry).filter_by(server_id=request.server_id_filter).first()
        if server:
            snippets.append(Snippet(
                server_id=server.server_id,
                snippet=f"Server {server.server_id}: {server.description}",
                content_hash=f"hash_{server.server_id}"
            ))
    else:
        servers = db.query(MCPServerRegistry).limit(5).all()
        for server in servers:
            snippets.append(Snippet(
                server_id=server.server_id,
                snippet=f"Server {server.server_id}: {server.description}",
                content_hash=f"hash_{server.server_id}"
            ))

    return AskRouteResponse(
        intent=intent,
        confidence=confidence,
        routing_target=routing_target,
        snippets=snippets,
        expanded_terms=expanded_terms
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    test_cases = [
        (
            {"question": "what servers have CRITICAL auth_strength?"},
            {"intent": "find_critical_axis", "routing_target": "axis_search"}
        ),
        (
            {"question": "compare server1 vs server2"},
            {"intent": "compare_servers", "routing_target": "server_comparison"}
        )
    ]

    all_passed = True
    for request_data, expected in test_cases:
        response = client.post("/ask/route", json=request_data)
        assert response.status_code == 200
        result = response.json()
        if result["intent"] != expected["intent"] or result["routing_target"] != expected["routing_target"]:
            print(f"FAIL: {request_data['question']}")
            print(f"Expected: {expected}")
            print(f"Got: {result}")
            all_passed = False

    if all_passed:
        print("PASS")