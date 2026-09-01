from sqlalchemy.pool import StaticPool
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
import csv
import io
from typing import List, Dict, Any

router = APIRouter()

def get_entities(session: Session) -> List[Dict[str, Any]]:
    entities = session.query(
        McpServerRegistry.id,
        McpServerRegistry.name,
        McpServerRegistry.org_id,
        McpServerRegistry.created_at,
        McpServerRegistry.updated_at,
        McpLlmAxisScore.score,
        McpLlmAxisScore.updated_at.label('score_updated_at')
    ).join(
        McpLlmAxisScore,
        McpServerRegistry.id == McpLlmAxisScore.server_id,
        isouter=True
    ).all()

    return [
        {
            'id': entity.id,
            'name': entity.name,
            'org_id': entity.org_id,
            'created_at': entity.created_at,
            'updated_at': entity.updated_at,
            'score': entity.score,
            'score_updated_at': entity.score_updated_at
        }
        for entity in entities
    ]

@router.get("/api/report/export")
async def export_entity_report(format: str, session: Session = Depends(get_session)):
    if format not in ['json', 'csv']:
        raise HTTPException(status_code=400, detail="Invalid format. Use 'json' or 'csv'.")

    entities = get_entities(session)

    if format == 'json':
        return entities
    elif format == 'csv':
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=entities[0].keys())
        writer.writeheader()
        writer.writerows(entities)
        output.seek(0)
        return {
            'filename': 'entity_report.csv',
            'content': output.read()
        }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import get_session
    from app.models import Base
    from sqlalchemy import create_engine

    # Set up in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    # Override the session dependency for testing
    app.dependency_overrides[get_session] = lambda: StaticPool(engine)

    # Create a test client
    client = TestClient(router)

    # Insert test data
    with engine.connect() as conn:
        conn.execute(
            "INSERT INTO McpServerRegistry (id, name, org_id, created_at, updated_at) VALUES (1, 'Test Server 1', 1, '2023-01-01', '2023-01-02')"
        )
        conn.execute(
            "INSERT INTO McpLlmAxisScore (server_id, score, updated_at) VALUES (1, 0.85, '2023-01-02')"
        )
        conn.execute(
            "INSERT INTO McpServerRegistry (id, name, org_id, created_at, updated_at) VALUES (2, 'Test Server 2', 2, '2023-01-03', '2023-01-04')"
        )
        conn.execute(
            "INSERT INTO McpLlmAxisScore (server_id, score, updated_at) VALUES (2, 0.90, '2023-01-04')"
        )
        conn.commit()

    # Test JSON export
    response = client.get("/api/report/export?format=json")
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.json()[0]['name'] == 'Test Server 1'
    assert response.json()[1]['name'] == 'Test Server 2'

    # Test CSV export
    response = client.get("/api/report/export?format=csv")
    assert response.status_code == 200
    assert 'entity_report.csv' in response.json()
    assert 'Test Server 1' in response.json()['content']
    assert 'Test Server 2' in response.json()['content']

    print("PASS")