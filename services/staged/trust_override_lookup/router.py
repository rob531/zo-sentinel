# services/staged/trust_override_lookup/schemas.py
from pydantic import BaseModel
from typing import Optional


class TrustOverrideResponse(BaseModel):
    server_id: str
    url: str
    name: str
    verdict: Optional[str]
    trusted: bool
    published_overall_risk: float
    criteria_version: str