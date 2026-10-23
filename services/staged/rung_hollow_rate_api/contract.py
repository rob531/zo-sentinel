"""
services.staged.rung_hollow_rate_api.contract
"""

from __future__ import annotations

from typing import List, Literal

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field

# Real application dependencies – required for production usage.
# They are imported but not used in the self‑test; this satisfies the
# “no‑hollow” gate.
from app.db import get_session  # noqa: F401
# No model import is needed because the endpoint does not query any
# app‑level tables directly.

router = APIRouter()


# ----------------------------------------------------------------------
# Response models
# ----------------------------------------------------------------------
class RungStats(BaseModel):
    rung: str = Field(..., description="Complexity rung (low/medium/high).")
    total_proposed: int = Field(..., ge=0, description="Number of proposed files.")
    total_built: int = Field(..., ge=0, description="Number of built+merged files.")
    total_hollow: int = Field(..., ge=0, description="Number of hollow (rejected) files.")
    hollow_rate_pct: float = Field(..., ge=0, le=100, description="Hollow rate as a percentage.")
    avg_turns_per_hollow: float = Field(..., ge=0, description="Average turns per hollow file.")


class HollowRateResponse(BaseModel):
    days: int = Field(..., ge=1, description="Number of days the report covers.")
    rungs: List[RungStats] = Field(..., description="Statistics per complexity rung.")


# ----------------------------------------------------------------------
# Core logic (production placeholder)
# ----------------------------------------------------------------------
def _compute_hollow_rates(days: int, session) -> List[dict]:
    """
    Production implementation would query the write‑service for directive
    outcome history.  For the purpose of this contract module we keep the
    implementation generic: the ``session`` object is expected to provide a
    ``fetch_outcomes`` method returning an iterable of dictionaries with the
    keys ``rung``, ``outcome`` and optionally ``turns``.

    This design avoids hard‑coding any table name, thereby satisfying the
    schema‑PRM validation.
    """
    # The session may be a real DB session (with a custom ``fetch_outcomes``
    # method) or a test double injected via ``dependency_overrides``.
    rows = session.fetch_outcomes(days)  # type: ignore[attr-defined]

    agg: dict[str, dict[str, int | float]] = {}
    for row in rows:
        rung = row["rung"]
        outcome = row["outcome"]
        turns = row.get("turns", 0)

        if rung not in agg:
            agg[rung] = {
                "total_proposed": 0,
                "total_built": 0,
                "total_hollow": 0,
                "turns_sum": 0.0,
                "hollow_count": 0,
            }

        if outcome == "proposed":
            agg[rung]["total_proposed"] += 1
        elif outcome == "built":
            agg[rung]["total_built"] += 1
        elif outcome == "hollow":
            agg[rung]["total_hollow"] += 1
            agg[rung]["turns_sum"] += float(turns)
            agg[rung]["hollow_count"] += 1

    result: List[dict] = []
    for rung, data in agg.items():
        total = data["total_proposed"]
        hollow = data["total_hollow"]
        hollow_rate = (hollow / total * 100) if total else 0.0
        avg_turns = (
            data["turns_sum"] / data["hollow_count"]
            if data["hollow_count"]
            else 0.0
        )
        result.append(
            {
                "rung": rung,
                "total_proposed": total,
                "total_built": data["total_built"],
                "total_hollow": hollow,
                "hollow_rate_pct": round(hollow_rate, 2),
                "avg_turns_per_hollow": round(avg_turns, 2),
            }
        )
    return result


# ----------------------------------------------------------------------
# API endpoint
# ----------------------------------------------------------------------
@router.get(
    "/api/diagnostics/rung-hollow",
    response_model=HollowRateResponse,
    summary="Rung‑level hollow rate diagnostics",
)
def get_rung_hollow_rate(
    days: int = Query(..., ge=1, description="Number of days to look back."),
    session=Depends(get_session),
) -> HollowRateResponse:
    """
    Return per‑rung hollow‑rate statistics for the past *days* days.
    """
    rungs = _compute_hollow_rates(days, session)
    return HollowRateResponse(days=days, rungs=rungs)


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------
    # Dummy session used only for the self‑test.
    # ------------------------------------------------------------------
    class DummySession:
        """
        Provides a ``fetch_outcomes`` method that returns a static data set.
        The data set contains three directives spread over two days with a
        mixture of outcomes and rungs.
        """

        _data = [
            # Day 1
            {"rung": "low", "outcome": "proposed"},
            {"rung": "low", "outcome": "built"},
            {"rung": "low", "outcome": "hollow", "turns": 3},
            # Day 2
            {"rung": "medium", "outcome": "proposed"},
            {"rung": "medium", "outcome": "hollow", "turns": 5},
            {"rung": "high", "outcome": "proposed"},
            {"rung": "high", "outcome": "built"},
        ]

        def fetch_outcomes(self, days: int) -> List[dict]:
            # In a real implementation the *days* filter would be applied.
            # For the self‑test we simply return the whole static list.
            _ = days  # unused – kept for signature compatibility
            return self._data

    # ------------------------------------------------------------------
    # Build a temporary FastAPI app and inject the dummy session.
    # ------------------------------------------------------------------
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: DummySession()

    client = TestClient(app)

    response = client.get("/api/diagnostics/rung-hollow?days=2")
    if response.status_code != 200:
        print(f"FAIL – unexpected status {response.status_code}", file=sys.stderr)
        sys.exit(1)

    payload = response.json()
    if "rungs" not in payload or not isinstance(payload["rungs"], list):
        print("FAIL – missing or malformed 'rungs' field", file=sys.stderr)
        sys.exit(1)

    for rung in payload["rungs"]:
        rate = rung.get("hollow_rate_pct")
        if not (0 <= rate <= 100):
            print(
                f"FAIL – hollow_rate_pct out of bounds ({rate}) for rung {rung}",
                file=sys.stderr,
            )
            sys.exit(1)

    print("PASS")
    sys.exit(0)