import re
from datetime import datetime
from enum import Enum
from typing import List, Optional

from fastapi import APIRouter, FastAPI, Query, HTTPException
from pydantic import BaseModel, Field
from fastapi.testclient import TestClient

# --- Pydantic Models and Enums ---

class RiskTier(str, Enum):
    TRUSTED_GENERAL = "TRUSTED_GENERAL"
    HIGH_RISK_ISOLATED = "HIGH_RISK_ISOLATED"
    # Add other risk tiers as needed for a real system

class DecisionStatus(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PENDING = "PENDING"
    # Add other decision statuses as needed for a real system

class MCPListingItem(BaseModel):
    mcp_id: str = Field(..., description="Unique identifier for the MCP.")
    mcp_name: str = Field(..., description="Name of the MCP.")
    current_verdict: str = Field(..., description="Current risk verdict (e.g., CRITICAL, HIGH, MEDIUM, LOW).")
    overall_risk_score: int = Field(..., description="Overall risk score (0-100).")
    last_decision_status: DecisionStatus = Field(..., description="Status of the last decision made for the MCP.")
    last_updated_at: datetime = Field(..., description="Timestamp of the last decision update.")

# --- Mock