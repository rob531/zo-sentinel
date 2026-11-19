"""HTTP surface for the trust-gate effect summary service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import trust_gate_effect_summary

router = APIRouter(prefix="/api/trust-gate", tags=["trust-gate"])


class TrustGateEffect(BaseModel):
    agree: int
    overridden: int
    missing_axes: int
    total: int
    override_rate: float


class TrustGateExample(BaseModel):
    server_id: str
    stored_verdict: str
    gated_verdict: str


class TrustGateEffectResponse(BaseModel):
    generated_at: datetime
    counts: TrustGateEffect
    examples: list[TrustGateExample]


@router.get("/effect-summary", response_model=TrustGateEffectResponse)
def get_trust_gate_effect_summary(
    db: Session = Depends(get_session),
) -> TrustGateEffectResponse:
    result = trust_gate_effect_summary(db)

    if isinstance(result, tuple):
        counts_data, examples_data = result
    elif isinstance(result, dict):
        counts_data = result.get("counts", result)
        examples_data = result.get("examples", [])
    else:
        counts_data = vars(result)
        examples_data = getattr(result, "examples", [])

    agree = int(counts_data.get("agree", 0))
    overridden = int(counts_data.get("overridden", 0))
    missing_axes = int(counts_data.get("missing_axes", 0))
    total = int(counts_data.get("total", agree + overridden + missing_axes))
    override_rate = float(
        counts_data.get(
            "override_rate",
            overridden / total if total else 0.0,
        )
    )

    examples = [
        TrustGateExample(
            server_id=str(example["server_id"]),
            stored_verdict=str(example["stored_verdict"]),
            gated_verdict=str(example["gated_verdict"]),
        )
        for example in list(examples_data)[:20]
    ]

    generated_at = (
        result.get("generated_at")
        if isinstance(result, dict)
        else getattr(result, "generated_at", None)
    )
    if generated_at is None:
        generated_at = datetime.now(timezone.utc)

    return TrustGateEffectResponse(
        generated_at=generated_at,
        counts=TrustGateEffect(
            agree=agree,
            overridden=overridden,
            missing_axes=missing_axes,
            total=total,
            override_rate=override_rate,
        ),
        examples=examples,
    )