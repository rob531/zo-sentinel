from fastapi import FastAPI, Depends
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

app = FastAPI()

def get_mcp_servers(session=Depends(get_session)):
    return session.query(McpServerRegistry).all()

def get_llm_scores(session=Depends(get_session)):
    return session.query(McpLlmAxisScore).all()

def get_score_disputes(session=Depends(get_session)):
    return session.query(McpScoreDispute).all()

def get_orgs(session=Depends(get_session)):
    return session.query(Org).all()

def get_users(session=Depends(get_session)):
    return session.query(User).all()

if __name__ == "__main__":
    from app.db import engine, Base
    from sqlalchemy.orm import sessionmaker
    from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Test data access
    servers = get_mcp_servers()
    scores = get_llm_scores()
    disputes = get_score_disputes()
    orgs = get_orgs()
    users = get_users()

    print("PASS")