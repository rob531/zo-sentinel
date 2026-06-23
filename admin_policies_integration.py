"""
admin_policies_integration.py

Integration utility to wire admin_policies.html to the backend mcp_policy_rules table.
Uses write_service on port 8772 for DB access per section 5 wiring rules.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import httpx
import os

# Configuration for write_service
WRITE_SERVICE_URL = os.getenv("WRITE_SERVICE_URL", "http://localhost:8772")
MCP_POLICY_RULES_ENDPOINT = f"{WRITE_SERVICE_URL}/api/v1/policy-rules"

router = APIRouter(prefix="/api/v1/admin/policies", tags=["admin_policies"])


# Pydantic models for request/response
class PolicyRuleBase(BaseModel):
    rule_type: str = Field(..., description="Type of policy rule (allow, deny, filter, etc.)")
    pattern: str = Field(..., description="Pattern to match (regex, glob, or exact)")
    description: Optional[str] = Field(None, description="Human-readable description")
    priority: int = Field(default=0, description="Rule priority (higher = more priority)")
    enabled: bool = Field(default=True, description="Whether rule is active")
    category: Optional[str] = Field(None, description="Rule category")
    action: str = Field(..., description="Action to take (allow, block, log, etc.)")
    metadata: Optional[dict] = Field(default_factory=dict, description="Additional metadata")


class PolicyRuleCreate(PolicyRuleBase):
    pass


class PolicyRuleUpdate(BaseModel):
    rule_type: Optional[str] = None
    pattern: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None
    category: Optional[str] = None
    action: Optional[str] = None
    metadata: Optional[dict] = None


class PolicyRuleResponse(PolicyRuleBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PolicyRulesListResponse(BaseModel):
    items: List[PolicyRuleResponse]
    total: int
    page: int
    page_size: int


# HTTP client for write_service communication
def get_write_service_client() -> httpx.AsyncClient:
    """Get async HTTP client for write_service communication."""
    return httpx.AsyncClient(base_url=WRITE_SERVICE_URL, timeout=30.0)


async def call_write_service(method: str, path: str, **kwargs):
    """Make authenticated call to write_service."""
    async with get_write_service_client() as client:
        response = await client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()


# CRUD Utility Functions (for direct import use)
async def create_policy_rule(rule: PolicyRuleCreate) -> PolicyRuleResponse:
    """Create a new policy rule via write_service."""
    try:
        result = await call_write_service(
            "POST",
            "/api/v1/mcp-policy-rules",
            json=rule.model_dump()
        )
        return PolicyRuleResponse(**result)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"Failed to create policy rule: {str(e)}")


async def get_policy_rule(rule_id: int) -> PolicyRuleResponse:
    """Retrieve a policy rule by ID via write_service."""
    try:
        result = await call_write_service("GET", f"/api/v1/mcp-policy-rules/{rule_id}")
        return PolicyRuleResponse(**result)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Policy rule not found")
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


async def list_policy_rules(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: Optional[str] = None,
    enabled: Optional[bool] = None,
    rule_type: Optional[str] = None
) -> PolicyRulesListResponse:
    """List all policy rules with optional filtering."""
    params = {"page": page, "page_size": page_size}
    if category:
        params["category"] = category
    if enabled is not None:
        params["enabled"] = enabled
    if rule_type:
        params["rule_type"] = rule_type

    try:
        result = await call_write_service("GET", "/api/v1/mcp-policy-rules", params=params)
        return PolicyRulesListResponse(
            items=[PolicyRuleResponse(**item) for item in result.get("items", [])],
            total=result.get("total", 0),
            page=result.get("page", page),
            page_size=result.get("page_size", page_size)
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"Failed to list policy rules: {str(e)}")


async def update_policy_rule(rule_id: int, update: PolicyRuleUpdate) -> PolicyRuleResponse:
    """Update an existing policy rule."""
    try:
        update_data = {k: v for k, v in update.model_dump().items() if v is not None}
        result = await call_write_service(
            "PATCH",
            f"/api/v1/mcp-policy-rules/{rule_id}",
            json=update_data
        )
        return PolicyRuleResponse(**result)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Policy rule not found")
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


async def delete_policy_rule(rule_id: int) -> dict:
    """Delete a policy rule by ID."""
    try:
        await call_write_service("DELETE", f"/api/v1/mcp-policy-rules/{rule_id}")
        return {"message": "Policy rule deleted successfully", "id": rule_id}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Policy rule not found")
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


async def bulk_toggle_policy_rules(rule_ids: List[int], enabled: bool) -> dict:
    """Bulk enable/disable policy rules."""
    try:
        result = await call_write_service(
            "POST",
            "/api/v1/mcp-policy-rules/bulk-toggle",
            json={"rule_ids": rule_ids, "enabled": enabled}
        )
        return result
    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"Failed to bulk toggle rules: {str(e)}")


async def duplicate_policy_rule(rule_id: int) -> PolicyRuleResponse:
    """Duplicate an existing policy rule."""
    try:
        original = await get_policy_rule(rule_id)
        duplicate_data = PolicyRuleCreate(
            rule_type=original.rule_type,
            pattern=original.pattern,
            description=f"{original.description} (copy)" if original.description else "Duplicate rule",
            priority=original.priority,
            enabled=False,
            category=original.category,
            action=original.action,
            metadata=original.metadata
        )
        return await create_policy_rule(duplicate_data)
    except HTTPException:
        raise


async def reorder_policy_rules(rule_ids: List[int]) -> dict:
    """Reorder policy rules by updating their priorities."""
    try:
        result = await call_write_service(
            "POST",
            "/api/v1/mcp-policy-rules/reorder",
            json={"rule_ids": rule_ids}
        )
        return result
    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"Failed to reorder rules: {str(e)}")


# FastAPI Router endpoints
@router.post("/", response_model=PolicyRuleResponse, status_code=201)
async def create_policy_rule_endpoint(rule: PolicyRuleCreate):
    """Create a new policy rule."""
    return await create_policy_rule(rule)


@router.get("/", response_model=PolicyRulesListResponse)
async def list_policy_rules_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: Optional[str] = None,
    enabled: Optional[bool] = None,
    rule_type: Optional[str] = None
):
    """List all policy rules with pagination and filtering."""
    return await list_policy_rules(page, page_size, category, enabled, rule_type)


@router.get("/{rule_id}", response_model=PolicyRuleResponse)
async def get_policy_rule_endpoint(rule_id: int):
    """Get a specific policy rule by ID."""
    return await get_policy_rule(rule_id)


@router.patch("/{rule_id}", response_model=PolicyRuleResponse)
async def update_policy_rule_endpoint(rule_id: int, update: PolicyRuleUpdate):
    """Update an existing policy rule."""
    return await update_policy_rule(rule_id, update)


@router.delete("/{rule_id}")
async def delete_policy_rule_endpoint(rule_id: int):
    """Delete a policy rule."""
    return await delete_policy_rule(rule_id)


@router.post("/bulk-toggle")
async def bulk_toggle_endpoint(rule_ids: List[int], enabled: bool):
    """Bulk enable/disable policy rules."""
    return await bulk_toggle_policy_rules(rule_ids, enabled)


@router.post("/{rule_id}/duplicate", response_model=PolicyRuleResponse)
async def duplicate_policy_rule_endpoint(rule_id: int):
    """Duplicate an existing policy rule."""
    return await duplicate_policy_rule(rule_id)


@router.post("/reorder")
async def reorder_policy_rules_endpoint(rule_ids: List[int]):
    """Reorder policy rules."""
    return await reorder_policy_rules(rule_ids)


# Export utility functions for direct use
__all__ = [
    "router",
    "create_policy_rule",
    "get_policy_rule",
    "list_policy_rules",
    "update_policy_rule",
    "delete_policy_rule",
    "bulk_toggle_policy_rules",
    "duplicate_policy_rule",
    "reorder_policy_rules",
    "PolicyRuleCreate",
    "PolicyRuleUpdate",
    "PolicyRuleResponse",
    "PolicyRulesListResponse",
]