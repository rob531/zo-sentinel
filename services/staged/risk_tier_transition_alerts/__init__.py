from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import User
from typing import Optional
from pydantic import BaseModel

router = APIRouter()

class UserResponse(BaseModel):
    id: int
    username: str
    clerk_created_at: Optional[str] = None

@router.get("/users/{user_id}", response_model=UserResponse)
def get_user(user_id: int, db: Session = Depends(get_session)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return {"error": "User not found"}
    return {
        "id": user.id,
        "username": user.username,
        "clerk_created_at": user.clerk_created_at.isoformat() if user.clerk_created_at else None
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import get_session
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    # Test data
    from datetime import datetime
    from app.models import User

    test_user = User(
        id=1,
        username="testuser",
        clerk_created_at=datetime.now()
    )
    with SessionLocal() as db:
        db.add(test_user)
        db.commit()

    # Run self-test
    from fastapi.testclient import TestClient
    client = TestClient(app)
    response = client.get("/users/1")
    assert response.status_code == 200
    assert response.json()["username"] == "testuser"
    print("PASS")