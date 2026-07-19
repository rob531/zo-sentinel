from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores

router = APIRouter()

class AxisMismatch(BaseModel):
    server_id: int
    expected_axes: int
    actual_axes: int

class WaveVerificationReport(BaseModel):
    wave_id: int
    total_registered: int
    total_scored: int
    unscored_server_ids: List[int]
    missing_axes: List[AxisMismatch]
    stale_model_servers: List[int]
    generated_at: datetime

@router.get("/wave-verification-report", response_model=WaveVerificationReport)
async def get_wave_verification_report(
    wave_id: Optional[int] = None,
    session: Session = Depends(get_session)
):
    # Get the latest wave if wave_id is not provided
    if wave_id is None:
        latest_wave = session.query(MCPLLMAxisScores.wave_id).order_by(MCPLLMAxisScores.wave_id.desc()).first()
        if not latest_wave:
            raise HTTPException(status_code=404, detail="No waves found")
        wave_id = latest_wave.wave_id

    # Get all registered servers
    registered_servers = session.query(MCPServerRegistry.id).all()
    total_registered = len(registered_servers)
    registered_server_ids = {server.id for server in registered_servers}

    # Get all scored servers in the wave
    scored_servers = session.query(MCPLLMAxisScores.server_id).filter(MCPLLMAxisScores.wave_id == wave_id).all()
    total_scored = len(scored_servers)
    scored_server_ids = {server.server_id for server in scored_servers}

    # Find unscored servers
    unscored_server_ids = list(registered_server_ids - scored_server_ids)

    # Find servers with missing axes
    missing_axes = []
    for server in registered_servers:
        server_id = server.id
        expected_axes = session.query(MCPServerRegistry.axis_count).filter(MCPServerRegistry.id == server_id).scalar()
        actual_axes = session.query(MCPLLMAxisScores).filter(
            MCPLLMAxisScores.server_id == server_id,
            MCPLLMAxisScores.wave_id == wave_id
        ).count()

        if actual_axes < expected_axes:
            missing_axes.append(AxisMismatch(
                server_id=server_id,
                expected_axes=expected_axes,
                actual_axes=actual_axes
            ))

    # Find servers with stale models (assuming model_version is tracked)
    stale_model_servers = []
    for server in scored_servers:
        server_id = server.server_id
        latest_model_version = session.query(MCPServerRegistry.model_version).filter(MCPServerRegistry.id == server_id).scalar()
        scored_model_version = session.query(MCPLLMAxisScores.model_version).filter(
            MCPLLMAxisScores.server_id == server_id,
            MCPLLMAxisScores.wave_id == wave_id
        ).scalar()

        if scored_model_version != latest_model_version:
            stale_model_servers.append(server_id)

    return WaveVerificationReport(
        wave_id=wave_id,
        total_registered=total_registered,
        total_scored=total_scored,
        unscored_server_ids=unscored_server_ids,
        missing_axes=missing_axes,
        stale_model_servers=stale_model_servers,
        generated_at=datetime.utcnow()
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Override dependencies for testing
    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Create test data
    with TestSessionLocal() as session:
        # Add a registered server
        server = MCPServerRegistry(
            id=1,
            axis_count=3,
            model_version="1.0"
        )
        session.add(server)

        # Add a scored server in wave 1
        scored_server = MCPLLMAxisScores(
            server_id=1,
            wave_id=1,
            model_version="1.0"
        )
        session.add(scored_server)
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/wave-verification-report")
    assert response.status_code == 200
    report = response.json()
    assert "wave_id" in report
    assert report["total_registered"] > 0
    assert report["total_scored"] >= 0
    assert isinstance(report["unscored_server_ids"], list)
    print("PASS")