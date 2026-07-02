from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import mcp_server_registry, mcp_llm_axis_scores, mcp_score_disputes, orgs, users
import requests

def score_ask_corpus_index(query_terms: list, session: Session = Depends(get_session)):
    field_weights = {'name': 4, 'verdict': 3, 'axis_labels': 2, 'description': 1}
    scored_docs = []

    servers = session.query(mcp_server_registry).all()
    for server in servers:
        score = 0
        matched_fields = []
        matched_terms = []

        for term in query_terms:
            if term in server.name:
                score += field_weights['name']
                matched_fields.append('name')
                matched_terms.append(term)
            if term in server.verdict:
                score += field_weights['verdict']
                matched_fields.append('verdict')
                matched_terms.append(term)
            if server.axis_labels and term in server.axis_labels:
                score += field_weights['axis_labels']
                matched_fields.append('axis_labels')
                matched_terms.append(term)
            if term in server.description:
                score += field_weights['description']
                matched_fields.append('description')
                matched_terms.append(term)

        if score > 0:
            scored_docs.append({
                'server_id': server.id,
                'score': score,
                'provenance': {
                    'matched_fields': list(set(matched_fields)),
                    'matched_terms': list(set(matched_terms))
                }
            })

    scored_docs.sort(key=lambda x: x['score'], reverse=True)
    top_k = scored_docs[:10]

    return top_k

def query_write_service(query: str):
    url = "http://127.0.0.1:8772/query"
    payload = {"query": query}
    response = requests.post(url, json=payload)
    return response.json()

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    test_server1 = mcp_server_registry(
        name="github server",
        verdict="weak auth",
        axis_labels="security, authentication",
        description="github server with weak authentication"
    )
    test_server2 = mcp_server_registry(
        name="aws server",
        verdict="strong auth",
        axis_labels="security, authentication",
        description="aws server with strong authentication"
    )
    test_server3 = mcp_server_registry(
        name="azure server",
        verdict="medium auth",
        axis_labels="security, authentication",
        description="azure server with medium authentication"
    )

    session.add_all([test_server1, test_server2, test_server3])
    session.commit()

    query_terms = ["weak", "auth", "github"]
    results = score_ask_corpus_index(query_terms, session)

    assert results[0]['server_id'] == test_server1.id
    assert results[0]['provenance']['matched_fields'] == ['name', 'verdict']
    assert results[0]['provenance']['matched_terms'] == ['weak', 'auth', 'github']

    print("PASS")