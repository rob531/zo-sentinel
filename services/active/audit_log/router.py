from fastapi import APIRouter, Depends, HTTPException
from app.db import get_session
from app.models import ProductAuditLog
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel

router = APIRouter(
    prefix="/api/product_audit_log",
    tags=["product_audit_log"],
    responses={404: {"description": "Not found"}},
)

class ProductAuditLogCreate(BaseModel):
    product_id: int
    action: str
    user_id: int
    details: Optional[str] = None

class ProductAuditLogResponse(BaseModel):
    id: int
    product_id: int
    action: str
    user_id: int
    timestamp: datetime
    details: Optional[str] = None

@router.get("/", response_model=List[ProductAuditLogResponse])
def get_product_audit_logs(
    skip: int = 0,
    limit: int = 100,
    product_id: Optional[int] = None,
    user_id: Optional[int] = None,
    db: Session = Depends(get_session)
):
    query = db.query(ProductAuditLog)
    if product_id:
        query = query.filter(ProductAuditLog.product_id == product_id)
    if user_id:
        query = query.filter(ProductAuditLog.user_id == user_id)
    return query.offset(skip).limit(limit).all()

@router.post("/", response_model=ProductAuditLogResponse)
def create_product_audit_log(
    log: ProductAuditLogCreate,
    db: Session = Depends(get_session)
):
    db_log = ProductAuditLog(
        product_id=log.product_id,
        action=log.action,
        user_id=log.user_id,
        details=log.details
    )
    db.add(db_log)
    db.commit()
    db.refresh(db_log)
    return db_log

if __name__ == "__main__":
    # Self-test
    from app.db import Base, engine
    from app.dependency_overrides import dependency_overrides

    # Use in-memory SQLite for testing
    from sqlalchemy import create_engine
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)

    # Override dependencies for testing
    dependency_overrides[get_session] = lambda: Session(test_engine)

    # Test data
    from app.models import ProductAuditLog
    test_session = Session(test_engine)
    test_log = ProductAuditLog(
        product_id=1,
        action="view",
        user_id=1,
        details="Viewed product details page"
    )
    test_session.add(test_log)
    test_session.commit()

    # Run tests
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    # Test GET endpoint
    response = client.get("/api/product_audit_log")
    assert response.status_code == 200
    assert len(response.json()) == 1

    # Test POST endpoint
    new_log = {
        "product_id": 2,
        "action": "purchase",
        "user_id": 1,
        "details": "Completed purchase"
    }
    response = client.post("/api/product_audit_log", json=new_log)
    assert response.status_code == 200
    assert response.json()["action"] == "purchase"

    print("Self-tests passed!")
