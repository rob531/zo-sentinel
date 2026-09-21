from fastapi import FastAPI, Depends
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

app = FastAPI()

def get_app_tables():
    session = Depends(get_session)
    return {
        "McpServerRegistry": session.query(McpServerRegistry).all(),
        "McpLlmAxisScore": session.query(McpLlmAxisScore).all(),
        "McpScoreDispute": session.query(McpScoreDispute).all(),
        "orgs": session.query(Org).all(),
        "users": session.query(User).all()
    }

def get_mesh_tables():
    import requests
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores, mesh_memory"})
    return response.json()

if __name__ == "__main__":
    from app.db import get_session
    from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override for self-test
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create tables for self-test
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    McpScoreDispute.__table__.create(engine)
    Org.__table__.create(engine)
    User.__table__.create(engine)

    # Test data access
    try:
        session = SessionLocal()
        session.query(McpServerRegistry).all()
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
    finally:
        session.close()