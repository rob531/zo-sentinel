from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import Base as AppBase
from elasticsearch import Elasticsearch
from elasticsearch_dsl import Document, Text, Keyword, Float as ESFloat
import os

router = APIRouter(prefix="/api")

class ExpansionTerm(BaseModel):
    term: str
    weight: float

class QueryExpansionResponse(BaseModel):
    query: str
    expansion: List[ExpansionTerm]

class AskCorpusIndex(Document):
    text = Text()
    term = Keyword()
    weight = ESFloat()

    class Index:
        name = 'ask_corpus_index'

def get_elasticsearch():
    return Elasticsearch([{'host': 'localhost', 'port': 9200}])

def seed_documents(session: Session):
    documents = [
        {"text": "This is a test document about security", "term": "security", "weight": 0.9},
        {"text": "Another test document about vulnerability", "term": "vulnerability", "weight": 0.8},
        {"text": "Final test document about risk", "term": "risk", "weight": 0.7}
    ]

    es = get_elasticsearch()
    AskCorpusIndex.init(using=es)

    for doc in documents:
        index_doc = AskCorpusIndex(**doc)
        index_doc.save(using=es)

@router.get("/ask/query/expand", response_model=QueryExpansionResponse)
def expand_query(query: str, session: Session = Depends(get_session), es: Elasticsearch = Depends(get_elasticsearch)):
    search = AskCorpusIndex.search(using=es).query("match", text=query)
    response = search.execute()

    expansion = []
    for hit in response:
        expansion.append(ExpansionTerm(term=hit.term, weight=hit.weight))

    return QueryExpansionResponse(query=query, expansion=expansion)

if __name__ == "__main__":
    from fastapi import FastAPI
    # FU-369: removed an import of `override_get_session` from a module that does not
    # exist in this tree. The name was never used in this file.

    app = FastAPI()
    app.include_router(router)

    # Setup test database
    SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    AppBase.metadata.create_all(bind=engine)

    def override_get_db():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_db

    # Seed test data
    with TestingSessionLocal() as session:
        seed_documents(session)

    # Run self-test
    client = TestClient(app)
    response = client.get("/api/ask/query/expand?query=security")

    assert response.status_code == 200
    data = response.json()
    assert any(term['term'] == 'security' for term in data['expansion'])

    print("PASS")