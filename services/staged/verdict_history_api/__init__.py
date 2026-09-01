from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

def get_server_registries(db: Session = Depends(get_session)):
    return db.query(McpServerRegistry).all()

def get_mesh_memory_by_id(mesh_memory_id: int, db: Session = Depends(get_session)):
    # Placeholder for mesh_memory query logic
    return {"id": mesh_memory_id, "data": "sample_data"}

def get_score_disputes_endpoint(db: Session = Depends(get_session)):
    return db.query(McpScoreDispute).all()

def mesh_memory_endpoint(db: Session = Depends(get_session)):
    # Placeholder for mesh_memory endpoint logic
    return {"status": "success"}

def signal_scores_endpoint(db: Session = Depends(get_session)):
    return db.query(McpLlmAxisScore).all()

def test_service_package():
    # Self-test for the service package
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.models import Base
    Base.metadata.create_all(engine)

    # Override the get_session dependency for testing
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: Session(engine)

    # Test the functions
    with Session(engine) as db:
        # Test get_server_registries
        registries = get_server_registries(db)
        assert isinstance(registries, list)

        # Test get_score_disputes_endpoint
        disputes = get_score_disputes_endpoint(db)
        assert isinstance(disputes, list)

        # Test signal_scores_endpoint
        scores = signal_scores_endpoint(db)
        assert isinstance(scores, list)

    print("PASS")

if __name__ == "__main__":
    test_service_package()