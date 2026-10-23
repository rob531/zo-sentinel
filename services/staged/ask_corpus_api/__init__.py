from typing import Optional
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

def get_server_registry(db: Session = Depends(get_session)) -> Optional[McpServerRegistry]:
    return db.query(McpServerRegistry).first()

def get_llm_axis_scores(db: Session = Depends(get_session)) -> list[McpLlmAxisScore]:
    return db.query(McpLlmAxisScore).all()

def get_score_disputes(db: Session = Depends(get_session)) -> list[McpScoreDispute]:
    return db.query(McpScoreDispute).all()

def get_orgs(db: Session = Depends(get_session)) -> list[Org]:
    return db.query(Org).all()

def get_users(db: Session = Depends(get_session)) -> list[User]:
    return db.query(User).all()

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    that_app = FastAPI()

    that_app.dependency_overrides[get_session] = lambda: Session(
        bind=None,
        autocommit=True,
        autoflush=False,
        expire_on_commit=False,
        info=None,
        extension=None,
        pool=None,
        pool_pre_ping=False,
        pool_recycle=None,
        pool_size=10,
        pool_timeout=30,
        poolclass=None,
        strategy=None,
        transactional=False,
        future=True,
    )

    client = TestClient(that_app)

    print("PASS")