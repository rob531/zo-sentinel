from sqlalchemy.pool import StaticPool
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import ServiceHealth, CodeNode
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api")

class RouterInfo(BaseModel):
    name: str
    age_seconds: int
    last_heartbeat: datetime

class RouterList(BaseModel):
    routers: List[RouterInfo]

def get_deferred_routers(db: Session = Depends(get_session)) -> RouterList:
    current_time = datetime.utcnow()
    routers = db.query(
        ServiceHealth.name,
        (current_time - ServiceHealth.last_heartbeat).label('age_seconds'),
        ServiceHealth.last_heartbeat
    ).join(
        CodeNode,
        and_(
            CodeNode.name == ServiceHealth.name,
            CodeNode.handler == 'build_service'
        )
    ).filter(
        ServiceHealth.status == 'running'
    ).all()

    return RouterList(
        routers=[
            RouterInfo(
                name=router.name,
                age_seconds=int(router.age_seconds.total_seconds()),
                last_heartbeat=router.last_heartbeat
            ) for router in routers
        ]
    )

router.get("/deferred/routers")(get_deferred_routers)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    engine = create_engine("sqlite:///:memory:", strategy="threadlocal")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependencies for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test client
    client = TestClient(router)

    # Seed test data
    from app.models import ServiceHealth, CodeNode
    from datetime import datetime, timedelta

    db = SessionLocal()
    db.add_all([
        ServiceHealth(
            name="router1",
            status="running",
            last_heartbeat=datetime.utcnow() - timedelta(seconds=200)
        ),
        ServiceHealth(
            name="router2",
            status="running",
            last_heartbeat=datetime.utcnow() - timedelta(seconds=50)
        ),
        CodeNode(
            name="router1",
            handler="build_service"
        ),
        CodeNode(
            name="router2",
            handler="build_service"
        )
    ])
    db.commit()

    # Test endpoint
    response = client.get("/deferred/routers")
    assert response.status_code == 200
    data = response.json()
    assert len(data["routers"]) == 2

    # Check at least one router has age > 100 seconds
    has_old_router = any(router["age_seconds"] > 100 for router in data["routers"])
    assert has_old_router

    print("PASS")