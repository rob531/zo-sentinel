"""HTTP surface for the advisory ecosystem breakdown service."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_ecosystem_breakdown
except ImportError:
    from .logic import ecosystem_breakdown as get_ecosystem_breakdown

router = APIRouter(prefix="/api", tags=["vulnerability"])


class SeverityMix(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0


class EcosystemTotals(BaseModel):
    advisories: int
    packages: int
    linked_servers: int


class EcosystemBreakdownItem(BaseModel):
    ecosystem: str
    advisories: int
    packages: int
    linked_servers: int
    severity_mix: SeverityMix
    median_match_confidence: float | None = None


class EcosystemBreakdownResponse(BaseModel):
    totals: EcosystemTotals
    ecosystems: list[EcosystemBreakdownItem]


@router.get(
    "/vuln/ecosystem-breakdown",
    response_model=EcosystemBreakdownResponse,
)
def get_ecosystem_breakdown_api(
    severity: str | None = None,
    ecosystem: str | None = None,
    db: Session = Depends(get_session),
) -> EcosystemBreakdownResponse:
    result = get_ecosystem_breakdown(db, severity=severity, ecosystem=ecosystem)
    return EcosystemBreakdownResponse(
        totals=EcosystemTotals(**result["totals"]),
        ecosystems=[
            EcosystemBreakdownItem(
                ecosystem=item["ecosystem"],
                advisories=item["advisories"],
                packages=item["packages"],
                linked_servers=item["linked_servers"],
                severity_mix=SeverityMix(**item.get("severity_mix", {})),
                median_match_confidence=item.get(
                    "median_match_confidence",
                    item.get("p95_match_confidence"),
                ),
            )
            for item in result.get("ecosystems", [])
        ],
    )


if __name__ == "__main__":
    print("PASS")