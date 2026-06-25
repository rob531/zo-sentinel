from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
import httpx
import asyncio
from datetime import datetime

app = FastAPI()

class PolicyRule(BaseModel):
    rule_type: str
    pattern: str
    action: str
    priority: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class PolicyRuleCreate(BaseModel):
    rule_type: str
    pattern: str
    action: str
    priority: int

class PolicyRuleUpdate(BaseModel):
    rule_type: Optional[str] = None
    pattern: Optional[str] = None
    action: Optional[str] = None
    priority: Optional[int] = None

async def _call_write_service(method: str, endpoint: str, data: Optional[dict] = None):
    async with httpx.AsyncClient() as client:
        url = f"http://127.0.0.1:8772{endpoint}"
        if method == "GET":
            response = await client.get(url)
        elif method == "POST":
            response = await client.post(url, json=data)
        elif method == "PUT":
            response = await client.put(url, json=data)
        elif method == "DELETE":
            response = await client.delete(url)
        else:
            raise ValueError(f"Unsupported method: {method}")

        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.text)
        return response.json()

@app.get("/policies", response_model=List[PolicyRule])
async def list_policies():
    data = await _call_write_service("GET", "/policies")
    return [PolicyRule(**item) for item in data]

@app.post("/policies", response_model=PolicyRule, status_code=status.HTTP_201_CREATED)
async def create_policy(rule: PolicyRuleCreate):
    data = await _call_write_service("POST", "/policies", rule.dict())
    return PolicyRule(**data)

@app.put("/policies/{rule_id}", response_model=PolicyRule)
async def update_policy(rule_id: int, rule: PolicyRuleUpdate):
    data = await _call_write_service("PUT", f"/policies/{rule_id}", rule.dict(exclude_unset=True))
    return PolicyRule(**data)

@app.delete("/policies/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(rule_id: int):
    await _call_write_service("DELETE", f"/policies/{rule_id}")

if __name__ == "__main__":
    import uvicorn
    from fastapi.testclient import TestClient

    client = TestClient(app)

    # Test data
    test_rule = {
        "rule_type": "test_type",
        "pattern": "test_pattern",
        "action": "test_action",
        "priority": 1
    }

    # Test creation
    response = client.post("/policies", json=test_rule)
    assert response.status_code == 201
    rule_id = response.json()["id"]

    # Test retrieval
    response = client.get("/policies")
    assert response.status_code == 200
    assert any(rule["id"] == rule_id for rule in response.json())

    # Test update
    update_data = {"action": "updated_action"}
    response = client.put(f"/policies/{rule_id}", json=update_data)
    assert response.status_code == 200
    assert response.json()["action"] == "updated_action"

    # Test deletion
    response = client.delete(f"/policies/{rule_id}")
    assert response.status_code == 204

    # Verify deletion
    response = client.get("/policies")
    assert response.status_code == 200
    assert not any(rule["id"] == rule_id for rule in response.json())

    print("PASS")