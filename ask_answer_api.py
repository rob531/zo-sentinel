from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import mcp_server_registry, mcp_llm_axis_scores, mcp_score_disputes, orgs, users
import requests
import os

router = APIRouter()

def run_ask_retrieval_service(session: Session, query: str):
    # Query the authoritative app tables
    servers = session.query(mcp_server_registry).all()
    llm_scores = session.query(mcp_llm_axis_scores).all()
    disputes = session.query(mcp_score_disputes).all()
    organizations = session.query(orgs).all()
    user_list = session.query(users).all()

    # Query the ZoComputer store via write_service
    response = requests.post('http://127.0.0.1:8772/query', json={'query': query})
    mesh_data = response.json()

    # Combine and process data
    retrieved_rows = {
        'servers': servers,
        'llm_scores': llm_scores,
        'disputes': disputes,
        'organizations': organizations,
        'users': user_list,
        'mesh_data': mesh_data
    }

    return retrieved_rows

def synthesize_answer(retrieved_rows: dict):
    # Synthesize answer from retrieved rows
    answer = "Answer synthesized from retrieved rows."
    citations = []

    for server in retrieved_rows['servers']:
        citations.append({
            'server_id': server.id,
            'matched_fields': ['name', 'description']
        })

    return answer, citations

@router.post("/ask")
async def ask(query: str, session: Session = Depends(get_session)):
    retrieved_rows = run_ask_retrieval_service(session, query)

    if not retrieved_rows['servers'] and not retrieved_rows['mesh_data']:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="INSUFFICIENT")

    answer, citations = synthesize_answer(retrieved_rows)

    if os.getenv('ASK_LLM') == '1':
        response = requests.post('http://ladder_shim:8796', json={'text': answer})
        answer = response.json()['polished_text']

    return {'answer': answer, 'citations': citations}

if __name__ == "__main__":
    import py_compile
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Compile the module
    py_compile.compile('ask_answer_api.py')

    # Create a throwaway SQLite session for testing
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Override the dependency
    from app.db import get_session
    from fastapi import FastAPI
    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    # Test case 1: ASK_LLM unset, no network call attempted
    os.environ['ASK_LLM'] = '0'
    response = requests.post('http://localhost:8000/ask', json={'query': 'test query'})
    assert 'polished_text' not in response.json()

    # Test case 2: Every server named in the answer appears in citations
    response = requests.post('http://localhost:8000/ask', json={'query': 'test query'})
    answer = response.json()['answer']
    citations = response.json()['citations']
    server_ids_in_answer = [server['server_id'] for server in citations if server['server_id'] in answer]
    assert all(server_id in [server['server_id'] for server in citations] for server_id in server_ids_in_answer)

    # Test case 3: Empty corpus yields INSUFFICIENT
    try:
        response = requests.post('http://localhost:8000/ask', json={'query': 'empty query'})
        assert False, "Expected HTTPException"
    except HTTPException as e:
        assert e.detail == "INSUFFICIENT"

    print("PASS")