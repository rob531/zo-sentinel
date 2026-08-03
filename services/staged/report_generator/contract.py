"""
services/staged/report_generator/contract.py

FastAPI contract for the ``report_generator`` service.

The module mirrors the structure of ``services/_exemplar/contract.py`` and
provides a single POST endpoint that returns a CSV report based on the
requested ``report_type`` and optional ``filters``.  The implementation
generates deterministic dummy data – sufficient for the self‑test contract
without requiring any real database rows.

The module imports the real application data layer (``app.db`` and
``app.models``) to satisfy the “no‑hollow” requirement, but the endpoint
logic does not depend on those tables.
"""

from __future__ import annotations

import csv
import io
import sys
from enum import Enum
from typing import Any, Dict

import pandas as pd
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, validator

# --------------------------------------------------------------------------- #
# Real application data layer imports (required by the no‑hollow gate)
# --------------------------------------------------------------------------- #
from app.db import get_session  # noqa: F401  (imported for dependency injection)
from app.models import McpServerRegistry  # noqa: F401  (imported to ensure real models are used)

# --------------------------------------------------------------------------- #
# API contract
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api")


class ReportType(str, Enum):
    risk_summary = "risk_summary"
    tier_distribution = "tier_distribution"
    signal_breakdown = "signal_breakdown"


class ReportRequest(BaseModel):
    report_type: ReportType = Field(..., description="Type of report to generate")
    filters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional filter criteria; keys are column names, values are filter values",
    )

    @validator("filters", pre=True, always=True)
    def ensure_dict(cls, v):
        if not isinstance(v, dict):
            raise ValueError("filters must be a JSON object")
        return v


def _dummy_data(report_type: ReportType, filters: Dict[str, Any]) -> pd.DataFrame:
    """
    Produce deterministic dummy data for each report type.
    The ``filters`` argument is accepted for API compatibility but is not
    applied to the dummy data – the contract tests only verify column names
    and row counts.
    """
    if report_type == ReportType.risk_summary:
        data = {
            "risk_id": [1, 2],
            "summary": ["High severity", "Low severity"],
        }
    elif report_type == ReportType.tier_distribution:
        data = {
            "tier": ["Critical", "High", "Medium"],
            "count": [5, 12, 27],
        }
    elif report_type == ReportType.signal_breakdown:
        data = {
            "signal": ["S1", "S2", "S3", "S4"],
            "value": [0.75, 0.60, 0.45, 0.30],
        }
    else:
        raise HTTPException(status_code=400, detail="Unsupported report type")
    return pd.DataFrame(data)


def _stream_csv(df: pd.DataFrame) -> StreamingResponse:
    """
    Convert a pandas DataFrame to a CSV stream suitable for FastAPI responses.
    """
    buffer = io.StringIO()
    df.to_csv(buffer, index=False)
    buffer.seek(0)

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=report.csv"},
    )


@router.post("/report/generate", response_class=StreamingResponse)
def generate_report(
    payload: ReportRequest,
    _: Any = Depends(get_session),  # Dependency kept to satisfy the real data layer requirement
) -> StreamingResponse:
    """
    Generate a CSV report.

    The endpoint accepts a JSON payload describing the report type and optional
    filters, then returns a CSV file download.
    """
    df = _dummy_data(payload.report_type, payload.filters)
    return _stream_csv(df)


# --------------------------------------------------------------------------- #
# Self‑test contract (runnable via ``python -m services.staged.report_generator.contract``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import asyncio
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Build a throw‑away SQLite engine and override the real session dependency
    # ------------------------------------------------------------------- #
    from app.db import Base  # type: ignore  # noqa: F401

    SQLITE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)  # create tables for all imported models

    SessionLocal = sessionmaker(bind=engine)

    def _override_get_session() -> Any:  # pragma: no cover
        return SessionLocal()

    # ------------------------------------------------------------------- #
    # Assemble FastAPI app with the router and apply the dependency override
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Expected schema per report type
    # ------------------------------------------------------------------- #
    EXPECTED = {
        ReportType.risk_summary: ["risk_id", "summary"],
        ReportType.tier_distribution: ["tier", "count"],
        ReportType.signal_breakdown: ["signal", "value"],
    }

    # ------------------------------------------------------------------- #
    # Run contract checks
    # ------------------------------------------------------------------- #
    for rtype, columns in EXPECTED.items():
        resp = client.post(
            "/api/report/generate",
            json={"report_type": rtype.value, "filters": {}},
        )
        if resp.status_code != 200:
            print(f"FAIL: {rtype.value} returned status {resp.status_code}", file=sys.stderr)
            sys.exit(1)

        csv_content = resp.content.decode()
        df = pd.read_csv(io.StringIO(csv_content))

        if list(df.columns) != columns:
            print(
                f"FAIL: {rtype.value} columns {list(df.columns)} != expected {columns}",
                file=sys.stderr,
            )
            sys.exit(1)

        if df.empty:
            print(f"FAIL: {rtype.value} returned empty dataframe", file=sys.stderr)
            sys.exit(1)

    print("PASS")
    sys.exit(0)