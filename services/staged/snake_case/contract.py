from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import re

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")

class SnakeCaseRequest(BaseModel):
    input_string: str

class SnakeCaseResponse(BaseModel):
    input_string: str
    snake_case_string: str

def to_snake_case(input_string: str) -> str:
    # Convert camelCase or PascalCase to snake_case
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', input_string)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

@router.get("/snake/case", response_model=SnakeCaseResponse)
async def convert_to_snake_case(input_string: str):
    snake_case_string = to_snake_case(input_string)
    return {"input_string": input_string, "snake_case_string": snake_case_string}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    app = FastAPI()
    app.include_router(router)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    client = TestClient(app)

    # Test cases
    test_cases = [
        ("camelCaseString", "camel_case_string"),
        ("PascalCaseString", "pascal_case_string"),
        ("already_snake_case", "already_snake_case"),
        ("MixedCase123", "mixed_case123"),
        ("", ""),
    ]

    for input_str, expected in test_cases:
        response = client.get(f"/api/snake/case?input_string={input_str}")
        assert response.status_code == 200
        assert response.json()["input_string"] == input_str
        assert response.json()["snake_case_string"] == expected

    print("PASS")