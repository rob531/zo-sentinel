from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

app = FastAPI()

def get_server_registry(db: Session = Depends(get_session)):
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
    from app.db import engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    test_db = SessionLocal()
    test_db.query(McpServerRegistry).all()
    test_db.query(McpLlmAxisScore).all()
    test_db.query(McpScoreDispute).all()
    test_db.query(Org).all()
    test_db.query(User).all()

    print("PASS")