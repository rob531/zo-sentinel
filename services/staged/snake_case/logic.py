from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import Optional
import re

router = APIRouter()

class SnakeCaseRequest(BaseModel):
    input_string: str

class SnakeCaseResponse(BaseModel):
    input_string: str
    snake_case_string: str

def to_snake_case(input_string: str) -> str:
    # Convert camelCase to snake_case
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', input_string)
    # Convert PascalCase to snake_case
    s2 = re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()
    return s2

@router.get("/api/snake/case", response_model=SnakeCaseResponse)
async def get_snake_case(input_string: str):
    snake_case_string = to_snake_case(input_string)
    return {"input_string": input_string, "snake_case_string": snake_case_string}

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import sqlite3

    app = FastAPI()
    app.include_router(router)

    # Create an in-memory SQLite database for testing
    engine = create_engine('sqlite:///:memory:')
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # Create tables in the in-memory database
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/snake/case?input_string=HelloWorld")
    assert response.status_code == 200
    assert response.json() == {"input_string": "HelloWorld", "snake_case_string": "hello_world"}

    response = client.get("/api/snake/case?input_string=helloWorld")
    assert response.status_code == 200
    assert response.json() == {"input_string": "helloWorld", "snake_case_string": "hello_world"}

    print("PASS")