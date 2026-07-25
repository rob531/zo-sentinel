from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPLLMAxisScores
from datetime import datetime
import json

router = APIRouter()

def get_gate_attribution_report(db: Session) -> dict:
    axes = ['overall_risk', 'auth_strength', 'capability_breadth',
            'data_sensitivity', 'network_egress', 'maintainer_trust',
            'exploit_surface']

    query = db.query(MCPLLMAxisScores.server_id,
                    MCPLLMAxisScores.axis_name,
                    MCPLLMAxisScores.p_top).filter(
        MCPLLMAxisScores.axis_name.in_(axes))

    results = query.all()

    axis_attribution = {}
    for axis in axes:
        axis_scores = [score.p_top for _, name, score in results if name == axis]
        if axis_scores:
            axis_attribution[axis] = sum(axis_scores) / len(axis_scores)
        else:
            axis_attribution[axis] = 0.0

    total_servers = len(set(server_id for server_id, _, _ in results))

    return {
        'total_servers': total_servers,
        'axis_attribution': axis_attribution,
        'timestamp': datetime.utcnow().isoformat()
    }

router.get("/gate-attribution/report")(get_gate_attribution_report)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.dependency_overrides import dependency_overrides

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    async def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    dependency_overrides[get_session] = override_get_session

    # Insert test data
    with SessionLocal() as session:
        test_data = [
            MCPLLMAxisScores(server_id=1, axis_name='overall_risk', p_top=0.8),
            MCPLLMAxisScores(server_id=1, axis_name='auth_strength', p_top=0.7),
            MCPLLMAxisScores(server_id=1, axis_name='capability_breadth', p_top=0.6),
            MCPLLMAxisScores(server_id=1, axis_name='data_sensitivity', p_top=0.5),
            MCPLLMAxisScores(server_id=1, axis_name='network_egress', p_top=0.4),
            MCPLLMAxisScores(server_id=1, axis_name='maintainer_trust', p_top=0.3),
            MCPLLMAxisScores(server_id=1, axis_name='exploit_surface', p_top=0.2),
            MCPLLMAxisScores(server_id=2, axis_name='overall_risk', p_top=0.7),
            MCPLLMAxisScores(server_id=2, axis_name='auth_strength', p_top=0.6),
            MCPLLMAxisScores(server_id=2, axis_name='capability_breadth', p_top=0.5),
            MCPLLMAxisScores(server_id=2, axis_name='data_sensitivity', p_top=0.4),
            MCPLLMAxisScores(server_id=2, axis_name='network_egress', p_top=0.3),
            MCPLLMAxisScores(server_id=2, axis_name='maintainer_trust', p_top=0.2),
            MCPLLMAxisScores(server_id=2, axis_name='exploit_surface', p_top=0.1),
        ]
        session.add_all(test_data)
        session.commit()

    client = TestClient(app)
    response = client.get("/gate-attribution/report")
    assert response.status_code == 200
    report = response.json()

    assert report['total_servers'] == 2
    assert len(report['axis_attribution']) == 7
    for axis in ['overall_risk', 'auth_strength', 'capability_breadth',
                 'data_sensitivity', 'network_egress', 'maintainer_trust',
                 'exploit_surface']:
        assert axis in report['axis_attribution']
        assert isinstance(report['axis_attribution'][axis], float)

    print("PASS")