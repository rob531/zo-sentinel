from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime
from app.db import get_session
from app.models import CircuitBreakerState, QualityMap
from sqlalchemy.orm import Session

router = APIRouter()

class QuarantinedFile(BaseModel):
    file: str
    quarantined_at: str
    reason: str
    on_disk: bool
    attempts: int
    max_attempts: int
    last_error: Optional[str]

class RetryBudget(BaseModel):
    file: str
    attempts: int
    max_attempts: int
    last_error: Optional[str]

class CircuitBreakerStatus(BaseModel):
    breaker_state: str
    quarantined: List[QuarantinedFile]
    retry_budget: List[RetryBudget]
    generated_at: str

@router.get("/admin/circuit-breaker", response_model=CircuitBreakerStatus)
async def get_circuit_breaker_status(db: Session = Depends(get_session)) -> Dict:
    # Get current breaker state
    breaker_state = db.query(CircuitBreakerState).first()
    if not breaker_state:
        raise HTTPException(status_code=404, detail="Circuit breaker state not found")

    # Get quality map data
    quality_map = db.query(QualityMap).all()

    quarantined_files = []
    retry_budget_files = []

    for qm in quality_map:
        if qm.quarantined:
            quarantined_files.append({
                "file": qm.file,
                "quarantined_at": qm.quarantined_at.isoformat(),
                "reason": qm.quarantine_reason,
                "on_disk": qm.on_disk,
                "attempts": qm.attempts,
                "max_attempts": qm.max_attempts,
                "last_error": qm.last_error
            })
        else:
            retry_budget_files.append({
                "file": qm.file,
                "attempts": qm.attempts,
                "max_attempts": qm.max_attempts,
                "last_error": qm.last_error
            })

    return {
        "breaker_state": breaker_state.state,
        "quarantined": quarantined_files,
        "retry_budget": retry_budget_files,
        "generated_at": datetime.utcnow().isoformat()
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    test_db = TestSession()
    test_db.add(CircuitBreakerState(state="OPEN"))
    test_db.add(QualityMap(
        file="test1.txt",
        quarantined=True,
        quarantined_at=datetime.utcnow(),
        quarantine_reason="Corrupt data",
        on_disk=True,
        attempts=3,
        max_attempts=5,
        last_error="Error reading file"
    ))
    test_db.add(QualityMap(
        file="test2.txt",
        quarantined=False,
        attempts=1,
        max_attempts=3,
        last_error=None
    ))
    test_db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/admin/circuit-breaker")
    assert response.status_code == 200
    data = response.json()
    assert "breaker_state" in data
    assert len(data["quarantined"]) > 0
    print("PASS")