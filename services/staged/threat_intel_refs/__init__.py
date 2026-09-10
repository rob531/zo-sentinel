from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

def get_server_export_api_quarantine_status(server_id: int, session: Session = Depends(get_session)) -> bool:
    """Check if a server is in export API quarantine."""
    server = session.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    return server.export_api_quarantine if server else False

def reset_server_export_api_quarantine(server_id: int, session: Session = Depends(get_session)) -> None:
    """Reset export API quarantine status for a server."""
    server = session.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if server:
        server.export_api_quarantine = False
        session.commit()

def get_signal_scores(server_id: int, session: Session = Depends(get_session)) -> list:
    """Get signal scores for a server."""
    return session.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()

def get_mesh_memory(server_id: int, session: Session = Depends(get_session)) -> dict:
    """Get mesh memory for a server."""
    # This is a placeholder for the actual implementation
    # that would query the ZoComputer store via http://127.0.0.1:8772/query
    return {"server_id": server_id, "mesh_memory": {}}

def get_mesh_scores(server_id: int, session: Session = Depends(get_session)) -> dict:
    """Get mesh scores for a server."""
    # This is a placeholder for the actual implementation
    # that would query the ZoComputer store via http://127.0.0.1:8772/query
    return {"server_id": server_id, "mesh_scores": {}}

def setup_database(session: Session = Depends(get_session)) -> None:
    """Setup the database."""
    # This is a placeholder for the actual implementation
    # that would setup the database
    pass

if __name__ == "__main__":
    from app.db import get_session
    from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create tables
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    McpScoreDispute.__table__.create(engine)
    Org.__table__.create(engine)
    User.__table__.create(engine)

    # Test the functions
    session = SessionLocal()
    server = McpServerRegistry(id=1, export_api_quarantine=True)
    session.add(server)
    session.commit()

    assert get_server_export_api_quarantine_status(1, session) == True
    reset_server_export_api_quarantine(1, session)
    assert get_server_export_api_quarantine_status(1, session) == False

    score = McpLlmAxisScore(server_id=1, score=0.5)
    session.add(score)
    session.commit()
    assert len(get_signal_scores(1, session)) == 1

    print("PASS")