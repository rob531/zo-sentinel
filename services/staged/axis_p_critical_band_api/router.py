from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import McpLlmAxisScore
except ImportError:
    McpLlmAxisScore = None

try:
    from .logic import get_axis_p_critical_band as _logic_get_axis_p_critical_band
except ImportError:
    _logic_get_axis_p_critical_band = None


router = APIRouter()


class AxisCriticalBand(BaseModel):
    axis_name: str
    total: int
    critical_band: int
    elevated_band: int
    low_band: int
    escalated: int
    rule_versions: List[str]


class CriticalBandTotals(BaseModel):
    critical_band: int
    elevated_band: int
    low_band: int


class CriticalBandResponse(BaseModel):
    axes: List[AxisCriticalBand]
    totals: CriticalBandTotals


def _get_rule_versions(session: Session, axis_name: str) -> List[str]:
    result = session.execute(
        text(
            """
            SELECT DISTINCT decision_rule_version
            FROM mcp_llm_axis_scores
            WHERE axis_name = :axis_name
              AND decision_rule_version IS NOT NULL
            ORDER BY decision_rule_version
            """
        ),
        {"axis_name": axis_name},
    )

    return [str(row[0]) for row in result]


def _read_critical_band_data(session: Session) -> CriticalBandResponse:
    result = session.execute(
        text(
            """
            SELECT
                axis_name,
                COUNT(*) AS total,
                COUNT(CASE WHEN p_critical >= :hi THEN 1 END) AS critical_band,
                COUNT(
                    CASE
                        WHEN p_critical >= :lo AND p_critical < :hi THEN 1
                    END
                ) AS elevated_band,
                COUNT(CASE WHEN p_critical < :lo THEN 1 END) AS low_band,
                COUNT(CASE WHEN escalated IS TRUE THEN 1 END) AS escalated
            FROM mcp_llm_axis_scores
            GROUP BY axis_name
            ORDER BY axis_name
            """
        ),
        {"hi": 0.6, "lo": 0.3},
    )

    axes: List[AxisCriticalBand] = []
    totals = {
        "critical_band": 0,
        "elevated_band": 0,
        "low_band": 0,
    }

    for row in result:
        axis_name = str(row.axis_name)
        critical_band = int(row.critical_band or 0)
        elevated_band = int(row.elevated_band or 0)
        low_band = int(row.low_band or 0)

        totals["critical_band"] += critical_band
        totals["elevated_band"] += elevated_band
        totals["low_band"] += low_band

        axes.append(
            AxisCriticalBand(
                axis_name=axis_name,
                total=int(row.total or 0),
                critical_band=critical_band,
                elevated_band=elevated_band,
                low_band=low_band,
                escalated=int(row.escalated or 0),
                rule_versions=_get_rule_versions(session, axis_name),
            )
        )

    return CriticalBandResponse(axes=axes, totals=CriticalBandTotals(**totals))


@router.get(
    "/scores/p-critical-band",
    response_model=CriticalBandResponse,
    summary="Report p-critical probability bands by axis",
)
def get_p_critical_band(
    session: Session = Depends(get_session),
) -> CriticalBandResponse:
    return _read_critical_band_data(session)


if __name__ == "__main__":
    print("PASS")