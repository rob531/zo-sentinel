from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, Org, User, ApiKey
from datetime import datetime
import jwt
from passlib.context import CryptContext

router = APIRouter()

# Pydantic models
class ServerRequest(BaseModel):
    server_id: int

class ServerResponse(BaseModel):
    server_id: int
    name: str
    url: str
    description: str
    trust_score: float
    verdict: str
    verdict_reasoning: str
    confidence: float
    risk_tier: str
    scan_count: int
    first_seen: datetime
    last_seen: datetime
    last_scanned: datetime
    last_assessed: datetime
    meta: dict

class AuthRequest(BaseModel):
    username: str
    password: str

class AuthResponse(BaseModel):
    access_token: str
    token_type: str

# Auth and RBAC
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def require_role(role: str):
    def wrapper(user: User = Depends(get_current_user)):
        if user.role != role:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return wrapper

# Endpoints
@router.get("/servers/{server_id}", response_model=ServerResponse)
def get_server(server_id: int, db: Session = Depends(get_session)):
    server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server

@router.post("/auth/login", response_model=AuthResponse)
def login(auth_request: AuthRequest):
    # Auth logic here
    pass

# Self-test
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Test client
    client = TestClient(app)

    # Test endpoints
    response = client.get("/servers/1")
    print(response.json())

    print("PASS")
