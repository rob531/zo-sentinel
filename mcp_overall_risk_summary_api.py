#!/usr/bin/env python3
"""
FastAPI router exposing GET /risk_summary.

The endpoint aggregates data from two MCP tables:

* `mcp_server_registry` – counts servers per `risk_tier` and the average
  `trust_score`.
* `mcp_llm_axis_scores` – counts servers where `escalated = TRUE` for each
  `axis_name`.

All DB access is performed through the local query service
`http://127.0.0.1:8772/query` using parameterised SQL (no user supplied
parameters in this case).

A small self‑test is executed when the module is run as ``__main__`` – it
patches the HTTP call to the query service, invokes the endpoint with a
FastAPI ``TestClient`` and asserts that the returned JSON contains the
expected keys and non‑negative integer counts.
"""

from __future__ import annotations

import json
from typing import List, Optional

import requests
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Pydantic models for the response
# --------------------------------------------------------------------------- #
class RiskTierSummary(BaseModel):
    """Summary for a single risk tier."""
    risk_tier: str = Field(..., description="Risk tier identifier")
    count: int = Field(..., ge=0, description="Number of servers in this tier")
    avg_trust_score: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Average trust score for the tier (0‑1 range)",
    )


class AxisEscalation(BaseModel):
    """Escalated server count for a single LLM risk axis."""
    axis_name: str = Field(..., description="Name of the risk axis")
    escalated_count: int = Field(..., ge=0, description="Servers with escalation=True")


class OverallRiskSummary(BaseModel):
    """Overall risk posture across all MCP servers."""
    total_servers: int = Field(..., ge=0, description="Total number of registered servers")
    risk_tiers: List[RiskTierSummary] = Field(..., description="Counts per risk tier")
    escalated_by_axis: List[AxisEscalation] = Field(
        ..., description="Escalated server counts per risk axis"
    )


# --------------------------------------------------------------------------- #
# Helper – thin wrapper around the query service
# --------------------------------------------------------------------------- #
_QUERY_ENDPOINT = "http://127.0.0.1:8772/query"


def _query_db(sql: str) -> List[dict]:
    """
    Execute a parameterised SQL query against the local query service.

    Parameters
    ----------
    sql: str
        The SQL statement to execute. No external parameters are injected,
        therefore the call is safe from injection attacks.

    Returns
    -------
    List[dict]
        List of rows (each row is a ``dict`` mapping column names to values).

    Raises
    ------
    HTTPException
        If the query service returns a non‑200 status or an unexpected payload.
    """
    try:
        resp = requests.post(_QUERY_ENDPOINT, json={"sql": sql}, timeout=5)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Query service error: {exc}")

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Query service returned HTTP {resp.status_code}",
        )

    try:
        payload = resp.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"Invalid JSON from query service: {exc}")

    # The contract of the query service is a JSON object with a ``rows`` key.
    rows = payload.get("rows")
    if rows is None or not isinstance(rows, list):
        raise HTTPException(status_code=502, detail="Malformed response from query service")
    return rows


# --------------------------------------------------------------------------- #
# FastAPI router
# --------------------------------------------------------------------------- #
router = APIRouter()


@router.get("/risk_summary", response_model=OverallRiskSummary)
def get_risk_summary() -> OverallRiskSummary:
    """
    Return a high‑level summary of the overall risk posture across all MCP servers.

    The response contains:
    * Total number of servers.
    * Per‑risk‑tier server counts and average trust scores.
    * Per‑axis counts of servers that have ``escalated = TRUE``.
    """
    # ------------------------------------------------------------------- #
    # 1. Risk tier aggregation (including average trust score)
    # ------------------------------------------------------------------- #
    tier_sql = """
        SELECT
            risk_tier,
            COUNT(*) AS count,
            AVG(trust_score) AS avg_trust_score
        FROM mcp_server_registry
        GROUP BY risk_tier
    """
    tier_rows = _query_db(tier_sql)

    risk_tiers: List[RiskTierSummary] = []
    total_servers = 0
    for row in tier_rows:
        # Defensive casting – the query service may return numbers as strings.
        count = int(row.get("count", 0))
        avg_score = row.get("avg_trust_score")
        avg_score = float(avg_score) if avg_score is not None else None
        risk_tiers.append(
            RiskTierSummary(
                risk_tier=str(row.get("risk_tier", "")),
                count=count,
                avg_trust_score=avg_score,
            )
        )
        total_servers += count

    # ------------------------------------------------------------------- #
    # 2. Escalated servers per axis
    # ------------------------------------------------------------------- #
    axis_sql = """
        SELECT
            axis_name,
            COUNT(*) AS escalated_count
        FROM mcp_llm_axis_scores
        WHERE escalated = TRUE
        GROUP BY axis_name
    """
    axis_rows = _query_db(axis_sql)

    escalated_by_axis: List[AxisEscalation] = []
    for row in axis_rows:
        escalated_by_axis.append(
            AxisEscalation(
                axis_name=str(row.get("axis_name", "")),
                escalated_count=int(row.get("escalated_count", 0)),
            )
        )

    # ------------------------------------------------------------------- #
    # Build and return the response model
    # ------------------------------------------------------------------- #
    return OverallRiskSummary(
        total_servers=total_servers,
        risk_tiers=risk_tiers,
        escalated_by_axis=escalated_by_axis,
    )


# --------------------------------------------------------------------------- #
# FastAPI app (router only – can be included in a larger application)
# --------------------------------------------------------------------------- #
app = FastAPI(title="MCP Overall Risk Summary API")
app.include_router(router)


# --------------------------------------------------------------------------- #
# Self‑test executed when the module is run directly
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # ------------------------------------------------------------------- #
    # TestClient based unit test – patches ``requests.post`` to avoid real
    # network calls.
    # ------------------------------------------------------------------- #
    from fastapi.testclient import TestClient
    from unittest.mock import patch

    # Mock payloads that mimic the query service responses
    def _mock_post(url, json, timeout=5):
        sql = json.get("sql", "").lower()
        # Registry aggregation response
        if "from mcp_server_registry" in sql:
            data = {
                "rows": [
                    {
                        "risk_tier": "TRUSTED_GENERAL",
                        "count": 5,
                        "avg_trust_score": 0.92,
                    },
                    {
                        "risk_tier": "HIGH_RISK_ISOLATED",
                        "count": 2,
                        "avg_trust_score": 0.31,
                    },
                ]
            }
        # Axis escalation response
        elif "from mcp_llm_axis_scores" in sql:
            data = {
                "rows": [
                    {"axis_name": "confidentiality", "escalated_count": 1},
                    {"axis_name": "integrity", "escalated_count": 0},
                ]
            }
        else:
            data = {"rows": []}

        class _Resp:
            def __init__(self, payload):
                self._payload = payload
                self.status_code = 200

            def json(self):
                return self._payload

        return _Resp(data)

    client = TestClient(app)

    with patch("requests.post", side_effect=_mock_post):
        response = client.get("/risk_summary")
        assert response.status_code == 200, f"Unexpected status {response.status_code}"
        payload = response.json()

        # Basic structural checks
        expected_keys = {"total_servers", "risk_tiers", "escalated_by_axis"}
        assert expected_keys.issubset(payload), f"Missing keys: {expected_keys - payload.keys()}"

        # total_servers must be non‑negative integer
        total = payload["total_servers"]
        assert isinstance(total, int) and total >= 0, "total_servers must be a non‑negative int"

        # risk_tiers list checks
        for item in payload["risk_tiers"]:
            assert "risk_tier" in item and "count" in item and "avg_trust_score" in item
            assert isinstance(item["count"], int) and item["count"] >= 0
            # avg_trust_score may be null
            if item["avg_trust_score"] is not None:
                assert isinstance(item["avg_trust_score"], (float, int))

        # escalated_by_axis list checks
        for item in payload["escalated_by_axis"]:
            assert "axis_name" in item and "escalated_count" in item
            assert isinstance(item["escalated_count"], int) and item["escalated_count"] >= 0

        print("PASS")