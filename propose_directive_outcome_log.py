from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import DirectiveOutcome
from sqlalchemy.orm import Session
from sqlalchemy import and_

router = APIRouter()

class DirectiveOutcomeResponse(BaseModel):
    timestamp: datetime
    task_name: str
    handler: str
    description: str
    status: str

class DirectiveOutcomeFilter(BaseModel):
    task: Optional[str] = None
    handler: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    start_timestamp: Optional[datetime] = None
    end_timestamp: Optional[datetime] = None

@router.get("/directives/outcomes", response_model=List[DirectiveOutcomeResponse])
async def get_directive_outcomes(
    filter: DirectiveOutcomeFilter = Depends(),
    session: Session = Depends(get_session)
):
    query = session.query(DirectiveOutcome)

    if filter.task:
        query = query.filter(DirectiveOutcome.task_name == filter.task)
    if filter.handler:
        query = query.filter(DirectiveOutcome.handler == filter.handler)
    if filter.description:
        query = query.filter(DirectiveOutcome.description == filter.description)
    if filter.status:
        query = query.filter(DirectiveOutcome.status == filter.status)
    if filter.start_timestamp:
        query = query.filter(DirectiveOutcome.timestamp >= filter.start_timestamp)
    if filter.end_timestamp:
        query = query.filter(DirectiveOutcome.timestamp <= filter.end_timestamp)

    outcomes = query.all()

    return [
        DirectiveOutcomeResponse(
            timestamp=outcome.timestamp,
            task_name=outcome.task_name,
            handler=outcome.handler,
            description=outcome.description,
            status=outcome.status
        )
        for outcome in outcomes
    ]

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import DirectiveOutcome
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Create test data
    Base.metadata.create_all(bind=engine)
    test_session = TestSessionLocal()
    test_session.add_all([
        DirectiveOutcome(
            timestamp=datetime.now(),
            task_name="test_task_1",
            handler="test_handler_1",
            description="test_description_1",
            status="written"
        ),
        DirectiveOutcome(
            timestamp=datetime.now(),
            task_name="test_task_2",
            handler="test_handler_2",
            description="test_description_2",
            status="duplicate"
        )
    ])
    test_session.commit()

    # Test the endpoint
    client = TestClient(router)
    response = client.get("/directives/outcomes")
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert all(field in response.json()[0] for field in ["timestamp", "task_name", "handler", "description", "status"])

    print("PASS")