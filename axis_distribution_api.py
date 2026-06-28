from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore
from typing import Dict, Any

router = APIRouter(prefix='/api', tags=['axis'])

@router.get('/axis-distribution', response_model=Dict[str, Any])
async def get_axis_distribution(
    axis: str = 'overall_risk',
    model_version: str = None,
    db: Session = Depends(get_session)
):
    query = db.query(
        McpLlmAxisScore.label,
        func.count(McpLlmAxisScore.id).label('count')
    ).filter(
        McpLlmAxisScore.axis_name == axis
    )

    if model_version:
        query = query.filter(McpLlmAxisScore.model_version == model_version)
    else:
        subquery = db.query(
            McpLlmAxisScore.model_version,
            func.max(McpLlmAxisScore.scored_at).label('latest_scored_at')
        ).group_by(
            McpLlmAxisScore.model_version
        ).subquery()

        query = query.join(
            subquery,
            (McpLlmAxisScore.model_version == subquery.c.model_version) &
            (McpLlmAxisScore.scored_at == subquery.c.latest_scored_at)
        )

    result = query.group_by(McpLlmAxisScore.label).all()

    distribution = {label: count for label, count in result}
    total = sum(distribution.values())

    return {
        'axis': axis,
        'model_version': model_version,
        'total': total,
        'distribution': distribution
    }

if __name__ == '__main__':
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    with SessionLocal() as session:
        session.add_all([
            McpLlmAxisScore(id=1, server_id='server1', axis_name='overall_risk', label='CRITICAL', model_version='v3.0_40974559'),
            McpLlmAxisScore(id=2, server_id='server2', axis_name='overall_risk', label='HIGH', model_version='v3.0_40974559'),
            McpLlmAxisScore(id=3, server_id='server3', axis_name='overall_risk', label='HIGH', model_version='v3.0_40974559'),
            McpLlmAxisScore(id=4, server_id='server4', axis_name='overall_risk', label='MEDIUM', model_version='v3.0_40974559'),
        ])
        session.commit()

    client = TestClient(app)
    response = client.get('/api/axis-distribution?axis=overall_risk')
    assert response.status_code == 200
    data = response.json()
    assert data['distribution']['HIGH'] == 2
    assert data['distribution']['CRITICAL'] == 1
    print('PASS')