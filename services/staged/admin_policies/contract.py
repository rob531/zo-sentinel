"""
admin_policies service contract.
Manages policy rules via write_service at 127.0.0.1:8772.
"""
import json
from datetime import datetime
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class PolicyCreate(BaseModel):
    rule_type: str
    pattern: str
    action: str
    priority: int


class PolicyResponse(BaseModel):
    id: int
    rule_type: str
    pattern: str
    action: str
    priority: int
    created_at: Optional[str] = None


app = FastAPI()

WRITE_SERVICE_URL = "http://127.0.0.1:8772"


def _query_policies() -> list[dict]:
    """Query all policies from write_service."""
    payload = {
        "operation": "select_all",
        "table": "mcp_policy_rules"
    }
    response = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json=payload,
        timeout=5
    )
    response.raise_for_status()
    result = response.json()
    return result.get("data", [])


def _get_policy_by_id(policy_id: int) -> Optional[dict]:
    """Get a single policy by ID from write_service."""
    payload = {
        "operation": "select_where",
        "table": "mcp_policy_rules",
        "where": {"id": policy_id}
    }
    response = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json=payload,
        timeout=5
    )
    response.raise_for_status()
    result = response.json()
    data = result.get("data", [])
    return data[0] if data else None


def _create_policy(policy_data: dict) -> dict:
    """Create a new policy via write_service."""
    payload = {
        "operation": "insert",
        "table": "mcp_policy_rules",
        "data": policy_data
    }
    response = requests.post(
        f"{WRITE_SERVICE_URL}/write",
        json=payload,
        timeout=5
    )
    response.raise_for_status()
    return response.json()


def _update_policy(policy_id: int, policy_data: dict) -> dict:
    """Update an existing policy via write_service."""
    payload = {
        "operation": "update",
        "table": "mcp_policy_rules",
        "where": {"id": policy_id},
        "data": policy_data
    }
    response = requests.post(
        f"{WRITE_SERVICE_URL}/write",
        json=payload,
        timeout=5
    )
    response.raise_for_status()
    return response.json()


def _delete_policy(policy_id: int) -> dict:
    """Delete a policy via write_service."""
    payload = {
        "operation": "delete",
        "table": "mcp_policy_rules",
        "where": {"id": policy_id}
    }
    response = requests.post(
        f"{WRITE_SERVICE_URL}/write",
        json=payload,
        timeout=5
    )
    response.raise_for_status()
    return response.json()


@app.get("/api/admin/policies")
def get_policies() -> list[PolicyResponse]:
    """Return all policy rules."""
    rows = _query_policies()
    return [PolicyResponse(**row) for row in rows]


@app.post("/api/admin/policies", status_code=201)
def create_policy(policy: PolicyCreate) -> PolicyResponse:
    """Create a new policy rule."""
    data = policy.model_dump()
    data["created_at"] = datetime.utcnow().isoformat()
    result = _create_policy(data)
    return PolicyResponse(**result)


@app.put("/api/admin/policies/{policy_id}")
def update_policy(policy_id: int, policy: PolicyCreate) -> PolicyResponse:
    """Update an existing policy rule."""
    existing = _get_policy_by_id(policy_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Policy not found")
    
    data = policy.model_dump()
    data["created_at"] = existing.get("created_at")
    result = _update_policy(policy_id, data)
    return PolicyResponse(**result)


@app.delete("/api/admin/policies/{policy_id}")
def delete_policy(policy_id: int) -> dict:
    """Delete a policy rule."""
    existing = _get_policy_by_id(policy_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Policy not found")
    
    result = _delete_policy(policy_id)
    return result


if __name__ == "__main__":
    import sqlite3
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    
    # Setup in-memory SQLite database for testing
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE mcp_policy_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_type TEXT NOT NULL,
            pattern TEXT NOT NULL,
            action TEXT NOT NULL,
            priority INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    
    # Seed initial data
    cursor.execute(
        "INSERT INTO mcp_policy_rules (rule_type, pattern, action, priority, created_at) VALUES (?, ?, ?, ?, ?)",
        ("package_name", "^requests$", "allow", 100, "2024-01-01T00:00:00")
    )
    cursor.execute(
        "INSERT INTO mcp_policy_rules (rule_type, pattern, action, priority, created_at) VALUES (?, ?, ?, ?, ?)",
        ("cve", "^CVE-2021-", "block", 200, "2024-01-02T00:00:00")
    )
    conn.commit()
    
    def serialize_row(row):
        return dict(row)
    
    def mock_request_handler(url, json=None, timeout=None):
        class MockResponse:
            def __init__(self, data, status_code=200):
                self._data = data
                self.status_code = status_code
            
            def raise_for_status(self):
                if self.status_code >= 400:
                    raise Exception(f"HTTP {self.status_code}")
            
            def json(self):
                return self._data
        
        op = json.get("operation") if json else None
        table = json.get("table") if json else None
        
        if "/query" in url:
            if op == "select_all":
                cursor.execute("SELECT * FROM mcp_policy_rules")
                rows = [serialize_row(row) for row in cursor.fetchall()]
                return MockResponse({"data": rows})
            elif op == "select_where":
                where = json.get("where", {})
                conditions = " AND ".join([f"{k} = ?" for k in where.keys()])
                cursor.execute(f"SELECT * FROM mcp_policy_rules WHERE {conditions}", list(where.values()))
                rows = [serialize_row(row) for row in cursor.fetchall()]
                return MockResponse({"data": rows})
        
        elif "/write" in url:
            if op == "insert":
                data = json.get("data", {})
                cursor.execute(
                    "INSERT INTO mcp_policy_rules (rule_type, pattern, action, priority, created_at) VALUES (?, ?, ?, ?, ?)",
                    (data["rule_type"], data["pattern"], data["action"], data["priority"], data["created_at"])
                )
                conn.commit()
                policy_id = cursor.lastrowid
                cursor.execute("SELECT * FROM mcp_policy_rules WHERE id = ?", (policy_id,))
                row = cursor.fetchone()
                return MockResponse(serialize_row(row))
            elif op == "update":
                where = json.get("where", {})
                data = json.get("data", {})
                set_clause = ", ".join([f"{k} = ?" for k in data.keys()])
                values = list(data.values()) + list(where.values())
                cursor.execute(f"UPDATE mcp_policy_rules SET {set_clause} WHERE id = ?", values[:-1] + [where.get("id")])
                conn.commit()
                cursor.execute("SELECT * FROM mcp_policy_rules WHERE id = ?", (where.get("id"),))
                row = cursor.fetchone()
                return MockResponse(serialize_row(row))
            elif op == "delete":
                where = json.get("where", {})
                cursor.execute("DELETE FROM mcp_policy_rules WHERE id = ?", (where.get("id"),))
                conn.commit()
                return MockResponse({"deleted": True})
        
        return MockResponse({}, 404)
    
    # Patch requests.post to use our mock
    with patch("requests.post", side_effect=mock_request_handler):
        client = TestClient(app)
        
        # GET /api/admin/policies - verify 2 seeded policies
        response = client.get("/api/admin/policies")
        assert response.status_code == 200
        policies = response.json()
        assert len(policies) == 2, f"Expected 2 policies, got {len(policies)}"
        assert policies[0]["rule_type"] == "package_name"
        assert policies[1]["rule_type"] == "cve"
        
        # POST /api/admin/policies - create a new policy
        new_policy = {
            "rule_type": "domain",
            "pattern": "^evil\\.com$",
            "action": "block",
            "priority": 50
        }
        response = client.post("/api/admin/policies", json=new_policy)
        assert response.status_code == 201
        created = response.json()
        assert created["rule_type"] == "domain"
        assert created["priority"] == 50
        
        # Verify POST created the 3rd policy
        response = client.get("/api/admin/policies")
        assert len(response.json()) == 3
        
        # PUT /api/admin/policies/{policy_id} - modify priority
        policy_id = created["id"]
        updated_policy = {
            "rule_type": "domain",
            "pattern": "^evil\\.com$",
            "action": "block",
            "priority": 150
        }
        response = client.put(f"/api/admin/policies/{policy_id}", json=updated_policy)
        assert response.status_code == 200
        assert response.json()["priority"] == 150
        
        # DELETE /api/admin/policies/{policy_id} - remove the policy
        response = client.delete(f"/api/admin/policies/{policy_id}")
        assert response.status_code == 200
        
        # Verify DELETE left 2 policies
        response = client.get("/api/admin/policies")
        assert len(response.json()) == 2
    
    conn.close()
    print("PASS")