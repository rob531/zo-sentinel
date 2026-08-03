from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import User, Org

router = APIRouter()

@router.get("/orgs")
def get_orgs(session: Session = Depends(get_session)):
    orgs = session.query(Org).all()
    return {"orgs": [{"id": org.id, "name": org.name} for org in orgs]}

@router.get("/users")
def get_users(session: Session = Depends(get_session)):
    users = session.query(User).all()
    return {"users": [{"id": user.id, "email": user.email} for user in users]}

if __name__ == "__main__":
    import uvicorn
    from fastapi.testclient import TestClient
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

    app = APIRouter()
    app.include_router(router)

    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(app)

    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    def test_get_orgs():
        response = client.get("/orgs")
        assert response.status_code == 200
        assert response.json() == {"orgs": []}

    def test_get_users():
        response = client.get("/users")
        assert response.status_code == 200
        assert response.json() == {"users": []}

    test_get_orgs()
    test_get_users()
    print("PASS")

    uvicorn.run(test_app, host="127.0.0.1", port=8000)