from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

app = FastAPI()

# Database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class MCPDecision(Base):
    __tablename__ = "mcp_decisions"

    decision_id = Column(Integer, primary_key=True, index=True)
    mcp_name = Column(String)
    decision_status = Column(String)
    rationale = Column(Text)

Base.metadata.create_all(bind=engine)

# Pydantic models
class MCPDecisionResponse(BaseModel):
    decision_id: int
    mcp_name: str
    decision_status: str
    rationale: str

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Endpoint
@app.get("/mcp_decisions/{decision_id}", response_model=MCPDecisionResponse)
def read_mcp_decision(decision_id: int, db: SessionLocal = next(get_db())):
    db_decision = db.query(MCPDecision).filter(MCPDecision.decision_id == decision_id).first()
    if db_decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    return db_decision

# Test
if __name__ == "__main__":
    # Seed the in-memory database
    db = next(get_db())
    test_decision = MCPDecision(decision_id=1, mcp_name="Test MCP", decision_status="Approved", rationale="Test rationale")
    db.add(test_decision)
    db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/mcp_decisions/1")
    assert response.status_code == 200
    assert response.json() == {
        "decision_id": 1,
        "mcp_name": "Test MCP",
        "decision_status": "Approved",
        "rationale": "Test rationale"
    }
    print("PASS")