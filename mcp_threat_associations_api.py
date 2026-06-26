from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import create_engine, Column, String, Integer, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime

# Database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Database model
class MCPThreatAssociationDB(Base):
    __tablename__ = "mcp_threat_associations"

    id = Column(Integer, primary_key=True, index=True)
    mcp_name = Column(String, index=True)
    threat_id = Column(String, index=True)
    association_type = Column(String)
    severity = Column(String)
    associated_at = Column(DateTime)

# Pydantic models
class MCPThreatAssociation(BaseModel):
    mcp_name: str
    threat_id: str
    association_type: str
    severity: str
    associated_at: datetime

class MCPThreatAssociationCreate(MCPThreatAssociation):
    pass

# Create tables
Base.metadata.create_all(bind=engine)

# FastAPI router
router = APIRouter()

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/mcp_threat_associations", response_model=List[MCPThreatAssociation])
def read_threat_associations(db: Session = Depends(get_db)):
    return db.query(MCPThreatAssociationDB).all()

@router.get("/mcp_threat_associations/{mcp_name}", response_model=List[MCPThreatAssociation])
def read_threat_associations_by_mcp(mcp_name: str, db: Session = Depends(get_db)):
    associations = db.query(MCPThreatAssociationDB).filter(MCPThreatAssociationDB.mcp_name == mcp_name).all()
    if not associations:
        raise HTTPException(status_code=404, detail="MCP not found")
    return associations

# Test client
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    # Seed test data
    with SessionLocal() as db:
        test_data = [
            MCPThreatAssociationDB(
                mcp_name="mcp1",
                threat_id="threat1",
                association_type="type1",
                severity="high",
                associated_at=datetime.now()
            ),
            MCPThreatAssociationDB(
                mcp_name="mcp1",
                threat_id="threat2",
                association_type="type2",
                severity="medium",
                associated_at=datetime.now()
            ),
            MCPThreatAssociationDB(
                mcp_name="mcp2",
                threat_id="threat3",
                association_type="type1",
                severity="low",
                associated_at=datetime.now()
            )
        ]
        db.add_all(test_data)
        db.commit()

    client = TestClient(app)

    # Test endpoints
    response = client.get("/mcp_threat_associations")
    assert response.status_code == 200
    assert len(response.json()) == 3

    response = client.get("/mcp_threat_associations/mcp1")
    assert response.status_code == 200
    assert len(response.json()) == 2

    print("PASS")