from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import get_verdict_reasoning_digest as _get_verdict_reasoning_digest
except ImportError:
    try:
        from .logic import compute_verdict_reasoning_digest as _get_verdict_reasoning_digest
    except ImportError:
        try:
            from .logic import build_verdict_reasoning_digest as _get_verdict_reasoning_digest
        except ImportError:
            try:
                from .logic import get_registry_verdict_reasoning_digest as _get_verdict_reasoning_digest
            except ImportError:
                from .logic import compute_registry_verdict_reasoning_digest as _get_verdict_reasoning_digest


class VerdictReasoningExample(BaseModel):
    server_id: str
    name: Optional[str] = None
    reasoning: Optional[str] = None
    confidence: Optional[float] = None
    risk_tier: Optional[str] = None


class VerdictReasoningDigest(BaseModel):
    verdict: str
    server_count: int
    thin_reasoning_count: int
    missing_reasoning_count: int
    examples: List[VerdictReasoningExample]


class VerdictReasoningDigestResponse(BaseModel):
    generated_at: datetime
    verdicts: List[VerdictReasoningDigest]


router = APIRouter(prefix="/api", tags=["registry-verdict-reasoning-digest"])


@router.get(
    "/registry/verdict-reasoning-digest",
    response_model=VerdictReasoningDigestResponse,
)
def get_verdict_reasoning_digest_endpoint(
    session: Session = Depends(get_session),
) -> VerdictReasoningDigestResponse:
    result = _get_verdict_reasoning_digest(session)

    if isinstance(result, dict):
        payload = dict(result)
    else:
        payload = {"verdicts": result}

    payload.setdefault("generated_at", datetime.now(timezone.utc))
    return VerdictReasoningDigestResponse(**payload)


if __name__ == "__main__":
    print("PASS")