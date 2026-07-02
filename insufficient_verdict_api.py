from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpSignalScores
from sqlalchemy.orm import Session
import requests

router = APIRouter()

class MissingSignal(BaseModel):
    signal_name: str
    source: str

class InsufficientVerdict(BaseModel):
    mcp_name: str
    server_id: str
    missing_signals: List[MissingSignal]

@router.get("/api/v1/insufficient_verdicts", response_model=List[InsufficientVerdict])
async def get_insufficient_verdicts(
    session: Session = Depends(get_session),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000)
):
    # Query McpServerRegistry for servers with 'INSUFFICIENT' verdict
    insufficient_servers = session.query(McpServerRegistry).filter(McpServerRegistry.verdict == 'INSUFFICIENT').offset(skip).limit(limit).all()

    # Prepare the response
    response = []
    for server in insufficient_servers:
        # Query McpSignalScores for missing signals
        missing_signals = []
        signal_scores = session.query(McpSignalScores).filter(McpSignalScores.server_id == server.server_id).all()
        for signal in signal_scores:
            if signal.score is None:
                missing_signals.append(MissingSignal(signal_name=signal.signal_name, source=signal.source))

        response.append(InsufficientVerdict(
            mcp_name=server.mcp_name,
            server_id=server.server_id,
            missing_signals=missing_signals
        ))

    return response

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from sqlalchemy.orm import sessionmaker

    # Create a throwaway SQLite session for testing
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    # Override the dependency
    from app.db import get_session
    from app.dependency_overrides import app
    app.dependency_overrides[get_session] = override_get_session

    # Create a test client
    client = TestClient(app)

    # Test the API
    response = client.get("/api/v1/insufficient_verdicts")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    # Clean up
    Base.metadata.drop_all(bind=engine)

    print("PASS")