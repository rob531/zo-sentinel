from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

def get_mcp_server_registry(session):
    return session.query(McpServerRegistry).all()

def get_mcp_llm_axis_scores(session):
    return session.query(McpLlmAxisScore).all()

def get_mcp_score_disputes(session):
    return session.query(McpScoreDispute).all()

def get_orgs(session):
    return session.query(Org).all()

def get_users(session):
    return session.query(User).all()

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Test data access
    session = SessionLocal()
    try:
        # Test each function
        get_mcp_server_registry(session)
        get_mcp_llm_axis_scores(session)
        get_mcp_score_disputes(session)
        get_orgs(session)
        get_users(session)
        print("PASS")
    finally:
        session.close()