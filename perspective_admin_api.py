from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import Perspective, User, Org
from app.rbac_enforcer import require_role
from app.write_service import write_audit_log
from typing import List

router = APIRouter(prefix="/perspectives", tags=["perspectives"])

@router.post("/", status_code=status.HTTP_201_CREATED)
def create_perspective(
    perspective: Perspective,
    session: Session = Depends(get_session),
    user: User = Depends(require_role("admin"))
):
    db_perspective = Perspective(**perspective.dict())
    session.add(db_perspective)
    session.commit()
    session.refresh(db_perspective)
    write_audit_log(
        action="create",
        entity_type="perspective",
        entity_id=db_perspective.id,
        user_id=user.id,
        org_id=user.org_id
    )
    return db_perspective

@router.put("/{perspective_id}", status_code=status.HTTP_200_OK)
def update_perspective(
    perspective_id: int,
    perspective: Perspective,
    session: Session = Depends(get_session),
    user: User = Depends(require_role("admin"))
):
    db_perspective = session.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not db_perspective:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Perspective not found")
    for key, value in perspective.dict().items():
        setattr(db_perspective, key, value)
    session.commit()
    write_audit_log(
        action="update",
        entity_type="perspective",
        entity_id=db_perspective.id,
        user_id=user.id,
        org_id=user.org_id
    )
    return db_perspective

@router.delete("/{perspective_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_perspective(
    perspective_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(require_role("admin"))
):
    db_perspective = session.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not db_perspective:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Perspective not found")
    session.delete(db_perspective)
    session.commit()
    write_audit_log(
        action="delete",
        entity_type="perspective",
        entity_id=perspective_id,
        user_id=user.id,
        org_id=user.org_id
    )
    return None

@router.get("/{perspective_id}", status_code=status.HTTP_200_OK)
def get_perspective(
    perspective_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(require_role("member"))
):
    db_perspective = session.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not db_perspective:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Perspective not found")
    return db_perspective

@router.get("/", status_code=status.HTTP_200_OK)
def list_perspectives(
    session: Session = Depends(get_session),
    user: User = Depends(require_role("member"))
):
    db_perspectives = session.query(Perspective).filter(Perspective.org_id == user.org_id).all()
    return db_perspectives

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Test member create is rejected
    response = client.post("/perspectives/", json={"name": "Test Perspective", "facet_filters": "test"})
    assert response.status_code == 403

    # Test admin create and list round-trips the saved facet_filters
    admin_user = User(email="admin@example.com", org_id=1)
    db = TestingSessionLocal()
    db.add(admin_user)
    db.commit()
    db.close()

    response = client.post("/perspectives/", json={"name": "Test Perspective", "facet_filters": "test"}, headers={"user-id": "1"})
    assert response.status_code == 201
    perspective_id = response.json()["id"]

    response = client.get("/perspectives/", headers={"user-id": "1"})
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["facet_filters"] == "test"

    print("PASS")