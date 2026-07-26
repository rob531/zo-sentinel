from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

app = FastAPI()

def get_mcp_servers(db: Session = Depends(get_session)):
    return db.query(McpServerRegistry).all()

def get_llm_axis_scores(db: Session = Depends(get_session)):
    return db.query(McpLlmAxisScore).all()

def get_score_disputes(db: Session = Depends(get_session)):
    return db.query(McpScoreDispute).all()

def get_orgs(db: Session = Depends(get_session)):
    return db.query(Org).all()

def get_users(db: Session = Depends(get_session)):
    return db.query(User).all()

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    # Test that all functions return without error
    get_mcp_servers()
    get_llm_axis_scores()
    get_score_disputes()
    get_orgs()
    get_users()

    print("PASS")