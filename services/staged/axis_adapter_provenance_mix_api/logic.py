from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


ALLOWED_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


class AdapterProvenanceMixRow(BaseModel):
    adapter_sha256: str
    model_version: str
    rows: int
    servers: int
    share: float


class AdapterProvenanceMixResponse(BaseModel):
    total_rows: int
    by_adapter: List[AdapterProvenanceMixRow]
    axes_covered: List[str]


AxisAdapterProvenanceMixResponse = AdapterProvenanceMixResponse


def _validate_axis(axis: Optional[str]) -> None:
    if axis is not None and axis not in ALLOWED_AXES:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Unknown axis name",
                "axis": axis,
                "allowed_axes": list(ALLOWED_AXES),
            },
        )


def get_adapter_provenance_mix(
    session: Session,
    axis: Optional[str] = None,
    days: int = 30,
) -> dict:
    _validate_axis(axis)

    conditions = []
    if axis is not None:
        conditions.append(McpLlmAxisScore.axis_name == axis)

    cutoff = datetime.utcnow() - timedelta(days=days)
    conditions.append(McpLlmAxisScore.scored_at >= cutoff)

    adapter_value = func.coalesce(McpLlmAxisScore.adapter_sha256, "")
    model_value = func.coalesce(McpLlmAxisScore.model_version, "")

    grouped = (
        select(
            adapter_value.label("adapter_sha256"),
            model_value.label("model_version"),
            func.count().label("rows"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("servers"),
        )
        .where(*conditions)
        .group_by(adapter_value, model_value)
        .cte("adapter_groups")
    )

    rows = session.execute(
        select(
            grouped.c.adapter_sha256,
            grouped.c.model_version,
            grouped.c.rows,
            grouped.c.servers,
            func.sum(grouped.c.rows).over().label("total_rows"),
        )
        .select_from(grouped)
        .order_by(grouped.c.rows.desc(), grouped.c.adapter_sha256, grouped.c.model_version)
    ).all()

    if not rows:
        return {
            "total_rows": 0,
            "by_adapter": [],
            "axes_covered": [],
        }

    total_rows = int(rows[0].total_rows)
    by_adapter = [
        {
            "adapter_sha256": row.adapter_sha256,
            "model_version": row.model_version,
            "rows": int(row.rows),
            "servers": int(row.servers),
            "share": round(int(row.rows) / total_rows, 4) if total_rows else 0.0,
        }
        for row in rows
    ]

    axes_covered = [
        axis_name
        for axis_name in session.execute(
            select(McpLlmAxisScore.axis_name)
            .where(*conditions)
            .distinct()
            .order_by(McpLlmAxisScore.axis_name)
        )
        .scalars()
        .all()
        if axis_name is not None
    ]

    return {
        "total_rows": total_rows,
        "by_adapter": by_adapter,
        "axes_covered": axes_covered,
    }


def get_axis_adapter_provenance_mix(
    session: Session,
    axis: Optional[str] = None,
    days: int = 30,
) -> dict:
    return get_adapter_provenance_mix(session=session, axis=axis, days=days)


def get_adapter_provenance(
    session: Session,
    axis: Optional[str] = None,
    days: int = 30,
) -> dict:
    return get_adapter_provenance_mix(session=session, axis=axis, days=days)


if __name__ == "__main__":
    print("PASS")