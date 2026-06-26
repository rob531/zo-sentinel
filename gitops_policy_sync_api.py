from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import List, Optional
import asyncio
from datetime import datetime
import uuid

# Mock database and Git operations
class MockDatabase:
    def __init__(self):
        self.policy_rules = []
        self.sync_status = []

    async def get_policy_rules(self):
        return self.policy_rules

    async def update_policy_rules(self, rules: List[dict]):
        self.policy_rules = rules

    async def get_sync_status(self):
        return self.sync_status

    async def update_sync_status(self, status: dict):
        self.sync_status.append(status)

db = MockDatabase()

# Security
security = HTTPBearer()

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials.credentials != "valid_token":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

# Models
class PolicyRule(BaseModel):
    id: str
    name: str
    description: str
    rule: dict

class SyncStatus(BaseModel):
    id: str
    status: str
    start_time: datetime
    end_time: Optional[datetime] = None
    message: Optional[str] = None

class SyncRequest(BaseModel):
    rules: List[PolicyRule]

# API
app = FastAPI()

@app.post("/sync", response_model=SyncStatus, dependencies=[Depends(verify_token)])
async def trigger_sync(sync_request: SyncRequest):
    # Simulate sync operation
    sync_id = str(uuid.uuid4())
    start_time = datetime.now()

    # Update rules in "database"
    rules = [rule.dict() for rule in sync_request.rules]
    await db.update_policy_rules(rules)

    # Simulate Git operations
    await asyncio.sleep(1)  # Simulate delay

    end_time = datetime.now()
    status = "completed"
    message = "Sync completed successfully"

    # Update sync status
    sync_status = {
        "id": sync_id,
        "status": status,
        "start_time": start_time,
        "end_time": end_time,
        "message": message
    }
    await db.update_sync_status(sync_status)

    return sync_status

@app.get("/status", response_model=List[SyncStatus], dependencies=[Depends(verify_token)])
async def get_sync_status():
    return await db.get_sync_status()

@app.get("/rules", response_model=List[PolicyRule], dependencies=[Depends(verify_token)])
async def get_policy_rules():
    rules = await db.get_policy_rules()
    return [PolicyRule(**rule) for rule in rules]

# Self-test
async def test_sync():
    # Test trigger sync
    test_rule = PolicyRule(
        id=str(uuid.uuid4()),
        name="test_rule",
        description="Test rule",
        rule={"action": "allow", "resource": "*"}
    )
    response = await trigger_sync(SyncRequest(rules=[test_rule]))
    assert response.status == "completed"

    # Test get status
    status = await get_sync_status()
    assert len(status) >= 1

    # Test get rules
    rules = await get_policy_rules()
    assert len(rules) >= 1

# Run self-test
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
    asyncio.run(test_sync())