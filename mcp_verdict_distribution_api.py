from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from typing import Dict

# Constants for verdict tiers
VERDICT_TIERS = [
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT"
]

app = FastAPI()

# Database setup (configured for the environment, but we'll override for testing)
SQLALCHEMY_DATABASE_URL = "postgresql://user:pass@localhost/dbname"
engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/mcp/verdict_distribution")
async def get_verdict_distribution(db: Session = Depends(get_db)):
    """
    Returns the count of MCPs in each verdict tier.
    """
    # Initialize distribution with 0s to ensure all tiers are present in response
    distribution = {tier: 0 for tier in VERDICT_TIERS}
    
    # Postgres-portable SQL to count occurrences of each verdict
    query = text("SELECT verdict, COUNT(*) as count FROM mcp_risk_register GROUP BY verdict")
    result = db.execute(query).fetchall()
    
    for row in result:
        verdict, count = row
        if verdict in distribution:
            distribution[verdict] = count
            
    return distribution

if __name__ == "__main__":
    # --- Self-Test Setup ---
    from sqlalchemy import Column, String, Integer
    from sqlalchemy.ext.declarative import declarative_base

    # Use SQLite for the self-test to avoid network calls/external DB dependencies
    test_engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(bind=test_engine)
    Base = declarative_base()

    class MCPRiskRegister(Base):
        __tablename__ = "mcp_risk_register"
        id = Column(Integer, primary_key=True)
        verdict = Column(String)

    Base.metadata.create_all(bind=test_engine)

    # Dependency override for testing
    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    # Seed test data
    db = TestingSessionLocal()
    test_data = [
        MCPRiskRegister(verdict="TRUSTED_GENERAL"),
        MCPRiskRegister(verdict="TRUSTED_GENERAL"),
        MCPRiskRegister(verdict="TRUSTED_RESEARCH"),
        MCPRiskRegister(verdict="CAUTION_LIMITED"),
        MCPRiskRegister(verdict="CAUTION_LIMITED"),
        MCPRiskRegister(verdict="CAUTION_LIMITED"),
        MCPRiskRegister(verdict="KNOWN_THREAT"),
    ]
    db.add_all(test_data)
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.get("/mcp/verdict_distribution")
    
    assert response.status_code == 200
    data = response.json()
    
    # Assert all tiers are present
    for tier in VERDICT_TIERS:
        assert tier in data
        assert isinstance(data[tier], int)

    # Assert specific counts from seed data
    assert data["TRUSTED_GENERAL"] == 2
    assert data["TRUSTED_RESEARCH"] == 1
    assert data["CAUTION_LIMITED"] == 3
    assert data["KNOWN_THREAT"] == 1
    assert data["INSUFFICIENT"] == 0
    assert data["ENTERPRISE_CONTROLLED"] == 0
    assert data["HIGH_RISK_ISOLATED"] == 0

    print("PASS")