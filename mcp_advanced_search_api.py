from fastapi import FastAPI, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from fastapi.testclient import TestClient
import json

app = FastAPI()
Base = declarative_base()

# Database models
class MCPServerRegistry(Base):
    __tablename__ = 'mcp_server_registry'
    id = Column(Integer, primary_key=True)
    mcp_name = Column(String)
    signal_type = Column(String)
    signals = relationship("MCPSignalScores", back_populates="server")

class MCPSignalScores(Base):
    __tablename__ = 'mcp_signal_scores'
    id = Column(Integer, primary_key=True)
    server_id = Column(Integer, ForeignKey('mcp_server_registry.id'))
    score = Column(Float)
    timestamp = Column(Integer)
    server = relationship("MCPServerRegistry", back_populates="signals")

# Pydantic models
class MCPResponse(BaseModel):
    mcp_name: str
    signal_type: str
    latest_score: float

class AdvancedSearchParams(BaseModel):
    mcp_name: Optional[str] = None
    signal_type: Optional[str] = None
    min_score: Optional[float] = None
    max_score: Optional[float] = None
    page: int = 1
    per_page: int = 10

# Dependency to get DB session
def get_db():
    engine = create_engine("sqlite:///:memory:", echo=True)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Seed data for testing
def seed_test_data(db: Session):
    # Add test data to both tables
    server1 = MCPServerRegistry(mcp_name="Server1", signal_type="TypeA")
    server2 = MCPServerRegistry(mcp_name="Server2", signal_type="TypeB")
    server3 = MCPServerRegistry(mcp_name="Server3", signal_type="TypeA")

    db.add_all([server1, server2, server3])
    db.commit()

    # Add signal scores
    score1 = MCPSignalScores(server_id=server1.id, score=95.5, timestamp=1)
    score2 = MCPSignalScores(server_id=server1.id, score=96.0, timestamp=2)
    score3 = MCPSignalScores(server_id=server2.id, score=85.0, timestamp=1)
    score4 = MCPSignalScores(server_id=server3.id, score=75.5, timestamp=1)

    db.add_all([score1, score2, score3, score4])
    db.commit()

# API endpoint
@app.get("/mcp/search/advanced", response_model=List[MCPResponse])
async def advanced_search(
    mcp_name: Optional[str] = Query(None),
    signal_type: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None),
    max_score: Optional[float] = Query(None),
    page: int = Query(1),
    per_page: int = Query(10),
    db: Session = Depends(get_db)
):
    # Build the base query
    query = db.query(MCPServerRegistry).join(
        MCPSignalScores,
        MCPServerRegistry.id == MCPSignalScores.server_id
    ).group_by(MCPServerRegistry.id)

    # Apply filters
    if mcp_name:
        query = query.filter(MCPServerRegistry.mcp_name == mcp_name)
    if signal_type:
        query = query.filter(MCPServerRegistry.signal_type == signal_type)
    if min_score is not None:
        query = query.having("MAX(MCPSignalScores.score) >= :min_score").params(min_score=min_score)
    if max_score is not None:
        query = query.having("MAX(MCPSignalScores.score) <= :max_score").params(max_score=max_score)

    # Get total count for pagination
    total = query.count()

    # Apply pagination
    query = query.offset((page - 1) * per_page).limit(per_page)

    # Execute the query and format results
    results = query.all()

    response = []
    for server in results:
        latest_score = max(score.score for score in server.signals)
        response.append({
            "mcp_name": server.mcp_name,
            "signal_type": server.signal_type,
            "latest_score": latest_score
        })

    return response

if __name__ == "__main__":
    # Test client setup
    client = TestClient(app)

    # Seed test data
    with get_db() as db:
        seed_test_data(db)

    # Test cases
    # Test 1: Filter by mcp_name
    response = client.get("/mcp/search/advanced?mcp_name=Server1")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["mcp_name"] == "Server1"

    # Test 2: Filter by signal_type
    response = client.get("/mcp/search/advanced?signal_type=TypeA")
    assert response.status_code == 200
    assert len(response.json()) == 2

    # Test 3: Filter by score range
    response = client.get("/mcp/search/advanced?min_score=90&max_score=100")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["mcp_name"] == "Server1"

    # Test 4: Pagination
    response = client.get("/mcp/search/advanced?per_page=2")
    assert response.status_code == 200
    assert len(response.json()) == 2

    # Test 5: All filters combined
    response = client.get("/mcp/search/advanced?signal_type=TypeA&min_score=70")
    assert response.status_code == 200
    assert len(response.json()) == 2

    print("PASS")