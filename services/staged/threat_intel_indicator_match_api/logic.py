from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, ThreatIntelRef, VulnLink


router = APIRouter(prefix="/api", tags=["threat-intel"])


def _build_matches(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}

    for row in rows:
        server_id = str(row["server_id"])
        server_name = str(row["server_name"] or "")
        risk_tier = str(row["risk_tier"] or "")
        match_basis = str(row["match_basis"] or "")
        confidence = float(row["match_confidence"] or 0.0)
        pulse_name = str(row["pulse_name"] or "")

        key = (server_id, server_name, risk_tier, match_basis, confidence)
        match = grouped.get(key)
        if match is None:
            match = {
                "server_id": server_id,
                "server_name": server_name,
                "risk_tier": risk_tier,
                "match_basis": match_basis,
                "match_confidence": confidence,
                "pulse_names": [],
            }
            grouped[key] = match

        if pulse_name and pulse_name not in match["pulse_names"]:
            match["pulse_names"].append(pulse_name)

    return list(grouped.values())


def list_indicator_matches(
    session: Session,
    indicator_type: str | None = None,
    indicator_value: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))

    statement = (
        select(
            ThreatIntelRef.indicator_type.label("indicator_type"),
            ThreatIntelRef.indicator_value.label("indicator_value"),
            VulnLink.server_id.label("server_id"),
            McpServerRegistry.name.label("server_name"),
            McpServerRegistry.risk_tier.label("risk_tier"),
            VulnLink.match_basis.label("match_basis"),
            VulnLink.match_confidence.label("match_confidence"),
            ThreatIntelRef.pulse_name.label("pulse_name"),
        )
        .join(VulnLink, VulnLink.advisory_id == ThreatIntelRef.id)
        .join(McpServerRegistry, McpServerRegistry.server_id == VulnLink.server_id)
        .order_by(ThreatIntelRef.pulse_created.desc())
        .limit(limit)
    )

    if indicator_type is not None:
        statement = statement.where(ThreatIntelRef.indicator_type == indicator_type)
    if indicator_value is not None:
        statement = statement.where(ThreatIntelRef.indicator_value == indicator_value)

    rows = session.execute(statement).mappings().all()
    matches = _build_matches(rows)

    selected_type = indicator_type
    selected_value = indicator_value
    if selected_type is None and rows:
        selected_type = str(rows[0]["indicator_type"] or "")
    if selected_value is None and rows:
        selected_value = str(rows[0]["indicator_value"] or "")

    return {
        "indicator_type": selected_type or "",
        "indicator_value": selected_value or "",
        "matches": matches,
        "total": len(matches),
    }


@router.get("/threat-intel/indicator-match")
def indicator_match(
    indicator_type: str | None = Query(default=None),
    indicator_value: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return list_indicator_matches(
        session=session,
        indicator_type=indicator_type,
        indicator_value=indicator_value,
        limit=limit,
    )


app = FastAPI(title="Threat Intel Indicator Match API")
app.include_router(router)


if __name__ == "__main__":
    sample_rows = [
        {
            "indicator_type": "cve",
            "indicator_value": "CVE-2026-0001",
            "server_id": "server-1",
            "server_name": "Example Server One",
            "risk_tier": "high",
            "match_basis": "cve",
            "match_confidence": 0.97,
            "pulse_name": "Example Pulse",
        },
        {
            "indicator_type": "cve",
            "indicator_value": "CVE-2026-0001",
            "server_id": "server-2",
            "server_name": "Example Server Two",
            "risk_tier": "medium",
            "match_basis": "cve",
            "match_confidence": 0.88,
            "pulse_name": "Example Pulse",
        },
    ]
    sample_matches = _build_matches(sample_rows)
    assert len(sample_matches) == 2
    assert all("server_id" in match for match in sample_matches)
    assert all("match_confidence" in match for match in sample_matches)
    assert any(
        route.path == "/api/threat-intel/indicator-match"
        for route in app.routes
    )
    print("PASS")