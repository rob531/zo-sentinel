from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from rbac_enforcer import RBACEnforcer

router = APIRouter()

class PermissionCheckRequest(BaseModel):
    user_id: str
    resource: str
    action: str

class PermissionCheckResponse(BaseModel):
    has_permission: bool

rbac_enforcer = RBACEnforcer()

@router.post("/check_permission", response_model=PermissionCheckResponse)
async def check_permission(request: PermissionCheckRequest):
    try:
        has_permission = rbac_enforcer.check_permission(
            request.user_id,
            request.resource,
            request.action
        )
        return {"has_permission": has_permission}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.test_client import TestClient

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test cases
    test_cases = [
        # (user_id, resource, action, expected_result)
        ("admin", "dashboard", "read", True),
        ("admin", "dashboard", "write", True),
        ("editor", "dashboard", "read", True),
        ("editor", "dashboard", "write", True),
        ("viewer", "dashboard", "read", True),
        ("viewer", "dashboard", "write", False),
        ("guest", "dashboard", "read", False),
        ("guest", "dashboard", "write", False),
        ("admin", "settings", "read", True),
        ("admin", "settings", "write", True),
        ("editor", "settings", "read", True),
        ("editor", "settings", "write", False),
        ("viewer", "settings", "read", False),
        ("viewer", "settings", "write", False),
        ("guest", "settings", "read", False),
        ("guest", "settings", "write", False),
    ]

    all_passed = True
    for user_id, resource, action, expected in test_cases:
        response = client.post(
            "/check_permission",
            json={"user_id": user_id, "resource": resource, "action": action}
        )
        assert response.status_code == 200
        assert response.json()["has_permission"] == expected
        if not (response.status_code == 200 and response.json()["has_permission"] == expected):
            all_passed = False

    if all_passed:
        print("PASS")
    else:
        print("FAIL")