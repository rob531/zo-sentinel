from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from .write_service import WriteService

router = APIRouter()

class ToolHash(BaseModel):
    id: Optional[int] = None
    hash_value: str
    tool_name: str
    algorithm: str

class ToolHashCreate(BaseModel):
    hash_value: str
    tool_name: str
    algorithm: str

class ToolHashUpdate(BaseModel):
    hash_value: Optional[str] = None
    tool_name: Optional[str] = None
    algorithm: Optional[str] = None

@router.get("/tool_hashes", response_model=List[ToolHash])
def get_tool_hashes(db: Session = Depends()):
    return WriteService.get_all_tool_hashes(db)

@router.get("/tool_hashes/{hash_id}", response_model=ToolHash)
def get_tool_hash(hash_id: int, db: Session = Depends()):
    tool_hash = WriteService.get_tool_hash_by_id(db, hash_id)
    if not tool_hash:
        raise HTTPException(status_code=404, detail="Tool hash not found")
    return tool_hash

@router.post("/tool_hashes", response_model=ToolHash, status_code=status.HTTP_201_CREATED)
def create_tool_hash(tool_hash: ToolHashCreate, db: Session = Depends()):
    return WriteService.create_tool_hash(db, tool_hash)

@router.put("/tool_hashes/{hash_id}", response_model=ToolHash)
def update_tool_hash(hash_id: int, tool_hash: ToolHashUpdate, db: Session = Depends()):
    existing_hash = WriteService.get_tool_hash_by_id(db, hash_id)
    if not existing_hash:
        raise HTTPException(status_code=404, detail="Tool hash not found")
    return WriteService.update_tool_hash(db, hash_id, tool_hash)

@router.delete("/tool_hashes/{hash_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tool_hash(hash_id: int, db: Session = Depends()):
    existing_hash = WriteService.get_tool_hash_by_id(db, hash_id)
    if not existing_hash:
        raise HTTPException(status_code=404, detail="Tool hash not found")
    WriteService.delete_tool_hash(db, hash_id)

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    engine = create_engine("sqlite:///./test.db")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[Depends] = get_db

    client = TestClient(app)

    test_hash = {"hash_value": "abc123", "tool_name": "test_tool", "algorithm": "SHA-256"}

    response = client.post("/tool_hashes", json=test_hash)
    assert response.status_code == 201
    assert response.json()["hash_value"] == "abc123"

    response = client.get("/tool_hashes")
    assert response.status_code == 200
    assert len(response.json()) >= 1
    assert any(item["hash_value"] == "abc123" for item in response.json())