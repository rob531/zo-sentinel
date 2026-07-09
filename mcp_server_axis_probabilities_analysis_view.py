from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Dict, List
from app.db import get_session
from app.models import McpLlmAxisScores

router = APIRouter()

class AxisProbability(BaseModel):
    label: str
    p_top: float

class ServerAxisProbabilities(BaseModel):
    server_id: int
    axes: Dict[str, AxisProbability]
    total_count: int

@router.get("/analysis/server-axis-probabilities", response_model=List[ServerAxisProbabilities])
def get_server_axis_probabilities(db: Session = Depends(get_session)):
    results = db.query(
        McpLlmAxisScores.server_id,
        McpLlmAxisScores.axis,
        McpLlmAxisScores.label,
        McpLlmAxisScores.p_top
    ).all()

    server_data = {}
    for row in results:
        server_id = row.server_id
        axis = row.axis
        label = row.label
        p_top = row.p_top

        if server_id not in server_data:
            server_data[server_id] = {
                'axes': {},
                'total_count': 0
            }

        server_data[server_id]['axes'][axis] = {
            'label': label,
            'p_top': p_top
        }
        server_data[server_id]['total_count'] += 1

    response = []
    for server_id, data in server_data.items():
        response.append({
            'server_id': server_id,
            'axes': data['axes'],
            'total_count': data['total_count']
        })

    return response

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base, McpLlmAxisScores

    app = FastAPI()
    app.include_router(router)

    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    test_client = TestClient(app)

    with SessionLocal() as session:
        test_data = [
            McpLlmAxisScores(
                server_id=1,
                axis="axis1",
                label="label1",
                p_top=0.9
            ),
            McpLlmAxisScores(
                server_id=1,
                axis="axis2",
                label="label2",
                p_top=0.8
            ),
            McpLlmAxisScores(
                server_id=2,
                axis="axis1",
                label="label1",
                p_top=0.7
            ),
        ]
        session.add_all(test_data)
        session.commit()

    response = test_client.get("/analysis/server-axis-probabilities")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    for item in data:
        assert len(item['axes']) == 2
        assert item['total_count'] == 2

    print("PASS")