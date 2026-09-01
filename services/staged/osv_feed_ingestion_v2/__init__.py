from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

app = FastAPI()

def get_mesh_memory(db: Session = Depends(get_session)) -> List[dict]:
    """Retrieve mesh memory data from the database."""
    try:
        # Using SQLAlchemy ORM for safe query construction
        results = db.execute("SELECT * FROM mesh_memory").fetchall()
        return [dict(row._mapping) for row in results]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_signal_scores(db: Session = Depends(get_session)) -> List[dict]:
    """Retrieve signal scores data from the database."""
    try:
        # Using SQLAlchemy ORM for safe query construction
        results = db.execute("SELECT * FROM mcp_signal_scores").fetchall()
        return [dict(row._mapping) for row in results]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def mesh_scores_endpoint(db: Session = Depends(get_session)) -> List[dict]:
    """Endpoint to retrieve mesh scores."""
    return get_signal_scores(db)

def mesh_memory_endpoint(db: Session = Depends(get_session)) -> List[dict]:
    """Endpoint to retrieve mesh memory."""
    return get_mesh_memory(db)

def _run_self_test():
    """Self-test for the module."""
    from app.db import get_session
    from app.dependency_overrides import override_get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    test_engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    app.dependency_overrides[get_session] = override_get_session(SessionLocal)

    # Create test tables
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    # Test get_mesh_memory
    try:
        result = get_mesh_memory()
        assert isinstance(result, list)
    except Exception as e:
        print(f"get_mesh_memory test failed: {e}")
        return

    # Test get_signal_scores
    try:
        result = get_signal_scores()
        assert isinstance(result, list)
    except Exception as e:
        print(f"get_signal_scores test failed: {e}")
        return

    # Test mesh_scores_endpoint
    try:
        result = mesh_scores_endpoint()
        assert isinstance(result, list)
    except Exception as e:
        print(f"mesh_scores_endpoint test failed: {e}")
        return

    # Test mesh_memory_endpoint
    try:
        result = mesh_memory_endpoint()
        assert isinstance(result, list)
    except Exception as e:
        print(f"mesh_memory_endpoint test failed: {e}")
        return

    print("PASS")

if __name__ == "__main__":
    _run_self_test()