from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import select, and_
from sqlalchemy.orm import Session
from datetime import datetime

router = APIRouter()

class AuditLogEntry(BaseModel):
    timestamp: datetime
    event_type: str
    actor: str
    target_server_id: str
    details: str

class AuditLogResponse(BaseModel):
    entries: List[AuditLogEntry]
    total: int

def get_db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()

def create_tables():
    from sqlalchemy import Column, Integer, String, DateTime, Text, create_engine
    from sqlalchemy.ext.declarative import declarative_base
    Base = declarative_base()

    class AuditLog(Base):
        __tablename__ = 'audit_log'
        id = Column(Integer, primary_key=True)
        timestamp = Column(DateTime)
        event_type = Column(String)
        actor = Column(String)
        target_server_id = Column(String)
        details = Column(Text)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

@router.get("/audit_log", response_model=AuditLogResponse)
async def get_audit_log(
    event_type: Optional[str] = Query(None),
    actor: Optional[str] = Query(None),
    target_server_id: Optional[str] = Query(None),
    skip: int = Query(0),
    limit: int = Query(10),
    db: Session = Depends(get_db_session)
):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.ext.declarative import declarative_base

    Base = declarative_base()

    class AuditLog(Base):
        __tablename__ = 'audit_log'
        id = Column(Integer, primary_key=True)
        timestamp = Column(DateTime)
        event_type = Column(String)
        actor = Column(String)
        target_server_id = Column(String)
        details = Column(Text)

    query = select(AuditLog)

    if event_type:
        query = query.where(AuditLog.event_type == event_type)
    if actor:
        query = query.where(AuditLog.actor == actor)
    if target_server_id:
        query = query.where(AuditLog.target_server_id == target_server_id)

    total = db.execute(select([func.count()]).select_from(query.subquery())).scalar()

    query = query.offset(skip).limit(limit)
    result = db.execute(query)
    entries = [AuditLogEntry(**row._asdict()) for row in result]

    return {"entries": entries, "total": total}

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    create_tables()

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.ext.declarative import declarative_base

    Base = declarative_base()

    class AuditLog(Base):
        __tablename__ = 'audit_log'
        id = Column(Integer, primary_key=True)
        timestamp = Column(DateTime)
        event_type = Column(String)
        actor = Column(String)
        target_server_id = Column(String)
        details = Column(Text)

    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    # Seed the database
    from datetime import datetime
    db.execute(AuditLog.__table__.insert(), [
        {"timestamp": datetime.now(), "event_type": "login", "actor": "user1", "target_server_id": "server1", "details": "User logged in"},
        {"timestamp": datetime.now(), "event_type": "logout", "actor": "user1", "target_server_id": "server1", "details": "User logged out"},
        {"timestamp": datetime.now(), "event_type": "login", "actor": "user2", "target_server_id": "server2", "details": "User logged in"},
    ])
    db.commit()

    client = TestClient(app)

    # Test GET /audit_log
    response = client.get("/audit_log")
    assert response.status_code == 200
    assert len(response.json()["entries"]) == 3

    # Test filtering by event_type
    response = client.get("/audit_log?event_type=login")
    assert response.status_code == 200
    assert len(response.json()["entries"]) == 2

    # Test filtering by actor
    response = client.get("/audit_log?actor=user1")
    assert response.status_code == 200
    assert len(response.json()["entries"]) == 2

    # Test filtering by target_server_id
    response = client.get("/audit_log?target_server_id=server1")
    assert response.status_code == 200
    assert len(response.json()["entries"]) == 2

    # Test pagination
    response = client.get("/audit_log?skip=1&limit=1")
    assert response.status_code == 200
    assert len(response.json()["entries"]) == 1

    print("PASS")