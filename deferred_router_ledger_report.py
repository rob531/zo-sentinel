from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional
from app.db import get_session
from app.models import MCP_Server_Registry

router = APIRouter()

class RouterReportItem(BaseModel):
    name: str
    last_heartbeat: Optional[datetime]

@router.get("/reports/routers/deferred", response_model=List[RouterReportItem])
def get_deferred_routers_report(db: Session = Depends(get_session)):
    deferred_routers = db.query(MCP_Server_Registry).filter(MCP_Server_Registry.is_mounted == False).all()
    return [
        RouterReportItem(name=r.name, last_heartbeat=r.last_heartbeat) 
        for r in deferred_routers
    ]

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Seed data
    with TestingSessionLocal() as session:
        session.add(MCP_Server_Registry(name="router-alpha", is_mounted=True, last_heartbeat=datetime.now()))
        session.add(MCP_Server_Registry(name="router-beta", is_mounted=False, last_heartbeat=datetime.now()))
        session.add(MCP_Server_Registry(name="router-gamma", is_mounted=False, last_heartbeat=None))
        session.commit()

    response = client.get("/reports/routers/deferred")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert any(item["name"] == "router-beta" for item in data)
    
    print("PASS")