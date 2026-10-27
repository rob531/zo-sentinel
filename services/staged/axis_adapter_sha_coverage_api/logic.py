from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


CANONICAL_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def _adapter_coverage(
    db: Session,
    *,
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    query_session = session if session is not None else db

    statement = select(
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.adapter_sha256,
        McpLlmAxisScore.model_version,
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.server_id,
    )

    grouped: Dict[str, Dict[str, Any]] = {}

    for row in query_session.execute(statement).all():
        adapter_sha256 = row.adapter_sha256
        if adapter_sha256 is None:
            continue

        adapter = grouped.setdefault(
            str(adapter_sha256),
            {
                "adapter_sha256": str(adapter_sha256),
                "model_version": row.model_version,
                "axes": set(),
                "row_count": 0,
                "servers": set(),
                "first_scored_at": None,
                "last_scored_at": None,
            },
        )

        if row.axis_name is not None:
            adapter["axes"].add(str(row.axis_name))

        adapter["row_count"] += 1

        if row.server_id is not None:
            adapter["servers"].add(row.server_id)

        if row.scored_at is not None:
            if (
                adapter["first_scored_at"] is None
                or row.scored_at < adapter["first_scored_at"]
            ):
                adapter["first_scored_at"] = row.scored_at
            if (
                adapter["last_scored_at"] is None
                or row.scored_at > adapter["last_scored_at"]
            ):
                adapter["last_scored_at"] = row.scored_at

        if adapter["model_version"] is None and row.model_version is not None:
            adapter["model_version"] = row.model_version

    adapters: List[Dict[str, Any]] = []

    for adapter_sha256 in sorted(grouped):
        adapter = grouped[adapter_sha256]
        covered_axes = adapter["axes"]
        missing_axes = [
            axis for axis in CANONICAL_AXES if axis not in covered_axes
        ]

        adapters.append(
            {
                "adapter_sha256": adapter["adapter_sha256"],
                "model_version": adapter["model_version"],
                "axes": sorted(covered_axes),
                "row_count": adapter["row_count"],
                "server_count": len(adapter["servers"]),
                "first_scored_at": adapter["first_scored_at"],
                "last_scored_at": adapter["last_scored_at"],
                "missing_axes": missing_axes,
            }
        )

    return {
        "adapters": adapters,
        "adapter_count": len(adapters),
        "axis_count": len(CANONICAL_AXES),
    }


def get_adapter_coverage(
    db: Session = Depends(get_session),
    *,
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    return _adapter_coverage(db, session=session)


def get_axis_adapter_coverage(
    db: Session = Depends(get_session),
    *,
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    return _adapter_coverage(db, session=session)


def get_adapter_sha_coverage(
    db: Session = Depends(get_session),
    *,
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    return _adapter_coverage(db, session=session)


def get_axis_adapter_sha_coverage(
    db: Session = Depends(get_session),
    *,
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    return _adapter_coverage(db, session=session)


def build_adapter_coverage(
    db: Session = Depends(get_session),
    *,
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    return _adapter_coverage(db, session=session)


def get_coverage(
    db: Session = Depends(get_session),
    *,
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    return _adapter_coverage(db, session=session)


if __name__ == "__main__":
    assert len(CANONICAL_AXES) == 7
    assert len(set(CANONICAL_AXES)) == 7
    assert "exploit_surface" in CANONICAL_AXES
    print("PASS")