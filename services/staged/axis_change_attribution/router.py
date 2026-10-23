from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore
from .logic import get_axis_changes

router = APIRouter(prefix="/api")

@router.get("/axis/{server_id}/changes")
async def get_axis_change_attribution(
    server_id: int,
    session: Session = Depends(get_session)
):
    return await get_axis_changes(server_id, session)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import Base, engine
    from app.models import McpLlmAxisScore
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine

    # Override the session for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)

    def override_get_session():
        session = Session(test_engine)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    with Session(test_engine) as session:
        session.add_all([
            McpLlmAxisScore(
                server_id=1,
                axis_name="security",
                old_label="low",
                new_label="medium",
                date="2023-01-01"
            ),
            McpLlmAxisScore(
                server_id=1,
                axis_name="security",
                old_label="medium",
                new_label="high",
                date="2023-01-02"
            ),
            McpLlmAxisScore(
                server_id=1,
                axis_name="performance",
                old_label="low",
                new_label="medium",
                date="2023-01-01"
            )
        ])
        session.commit()

    client = TestClient(app)
    response = client.get("/api/axis/1/changes")
    assert response.status_code == 200
    data = response.json()
    assert len(data["axes"]) == 2
    security_axis = next(axis for axis in data["axes"] if axis["name"] == "security")
    assert len(security_axis["changes"]) == 2
    print("PASS")