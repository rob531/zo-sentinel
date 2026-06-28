# mcp_policy_violation_summary_api.py
from fastapi import APIRouter, FastAPI, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict, Any, Set
from fastapi.testclient import TestClient
import re

# --- Pydantic Models ---
class ViolatedRule(BaseModel):
    """Represents a policy rule that has been violated."""
    rule_type: str
    pattern: str

class PolicyViolationSummary(BaseModel):
    """Summary of policy violations for a given MCP."""
    mcp_id: int
    violated_rules: List[ViolatedRule]
    total_violations: int

# --- Database Service Interface (for dependency injection) ---
class WriteService:
    """
    Abstract interface for database write/read operations.
    In a real application, this would connect to a database.
    """
    async def read_sql(self, query: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Executes a read SQL query with parameterized values."""
        raise NotImplementedError("read_sql method must be implemented by concrete WriteService.")

# --- Dependency Injection Function ---
def get_write_service() -> WriteService:
    """
    Dependency provider for WriteService.
    This function will be overridden in tests.
    """
    # In a real application, this would return an actual database service instance.
    # For this example,