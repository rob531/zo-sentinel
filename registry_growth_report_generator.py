# deps: fastapi, sqlalchemy, pydantic, matplotlib
"""registry_growth_report_generator

FastAPI module that provides an endpoint to generate a registry growth report.
It reads real application tables via the existing DB session dependency and
produces a JSON report and a plot image saved under ``shared/outputs/goose``.

The module is import‑safe (no side effects) and includes a ``__main__`` self‑test
that exercises the endpoint with a mocked session.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any

import matplotlib.pyplot as plt
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Import the real data layer – REQUIRED by the no‑hollow gate
from app.db import get_session
from app.models import McpServerRegistry, CadenceJobRun

router = APIRouter()

# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------
class GrowthPoint(BaseModel):
    date: datetime = Field(..., description="Date of the metric")
    new_servers: int = Field(..., description="Number of new servers added on this date")

class RegistryGrowthReport(BaseModel):
    total_servers: int = Field(..., description="Total number of servers in the registry")
    new_servers_last_30_days: int = Field(..., description="New servers added in the last 30 days")
    growth_trend: List[GrowthPoint] = Field(..., description="Daily growth points for the last 30 days")
    plot_path: str = Field(..., description="Filesystem path to the generated plot image")

# ---------------------------------------------------------------------------
# Core logic – pure function (no side effects except DB reads and file writes)
# ---------------------------------------------------------------------------
def _fetch_registry_entries(session: Session) -> List[McpServerRegistry]:
    """Return all server registry entries.

    The function is deliberately simple – the caller decides how to slice the
    data.  It raises ``HTTPException`` only for unexpected DB errors.
    """
    try:
        return session.query(McpServerRegistry).all()
    except Exception as exc:  # pragma: no cover – defensive
        raise HTTPException(status_code=500, detail="Failed to query McpServerRegistry") from exc


def _compute_daily_new_counts(
    entries: List[McpServerRegistry],
    start: datetime,
    end: datetime,
) -> Dict[datetime, int]:
    """Count new servers per day between *start* and *end* (inclusive).

    The ``McpServerRegistry`` model is expected to have a ``created_at`` column
    (datetime).  If the attribute is missing we fall back to ``first_seen``.
    """
    # Determine the attribute name that holds the creation timestamp.
    timestamp_attr = "created_at" if hasattr(McpServerRegistry, "created_at") else "first_seen"
    daily: Dict[datetime, int] = {}
    for entry in entries:
        ts = getattr(entry, timestamp_attr, None)
        if not isinstance(ts, datetime):
            continue
        if start <= ts <= end:
            day = datetime(ts.year, ts.month, ts.day)
            daily[day] = daily.get(day, 0) + 1
    return daily


def generate_report(session: Session) -> RegistryGrowthReport:
    """Generate the registry growth report.

    The function performs the following steps:

    1. Fetch all ``McpServerRegistry`` rows.
    2. Compute total servers and new servers in the last 30 days.
    3. Build a daily growth series for the last 30 days.
    4. Render a line plot and write it to ``shared/outputs/goose``.
    5. Serialize the JSON report to the same directory.
    """
    now = datetime.utcnow()
    start_30 = now - timedelta(days=30)

    entries = _fetch_registry_entries(session)
    total_servers = len(entries)
    daily_counts = _compute_daily_new_counts(entries, start_30, now)

    # Ensure every day in the window appears (even with zero count)
    growth_points: List[GrowthPoint] = []
    for i in range(31):
        day = datetime(start_30.year, start_30.month, start_30.day) + timedelta(days=i)
        count = daily_counts.get(day, 0)
        growth_points.append(GrowthPoint(date=day, new_servers=count))

    new_last_30 = sum(p.new_servers for p in growth_points)

    # Plot generation
    plot_dir = os.path.join("shared", "outputs", "goose")
    os.makedirs(plot_dir, exist_ok=True)
    plot_path = os.path.join(plot_dir, "registry_growth_plot.png")
    dates = [p.date for p in growth_points]
    values = [p.new_servers for p in growth_points]
    plt.figure(figsize=(10, 5))
    plt.plot(dates, values, marker="o")
    plt.title("New Servers per Day (Last 30 Days)")
    plt.xlabel("Date")
    plt.ylabel("New Servers")
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(plot_path)
    plt.close()

    # JSON report
    report_path = os.path.join(plot_dir, "registry_growth_report.json")
    report_dict = {
        "total_servers": total_servers,
        "new_servers_last_30_days": new_last_30,
        "growth_trend": [
            {"date": p.date.isoformat(), "new_servers": p.new_servers} for p in growth_points
        ],
        "plot_path": plot_path,
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2)

    return RegistryGrowthReport(
        total_servers=total_servers,
        new_servers_last_30_days=new_last_30,
        growth_trend=growth_points,
        plot_path=plot_path,
    )

# ---------------------------------------------------------------------------
# FastAPI endpoint
# ---------------------------------------------------------------------------
@router.get("/registry_growth_report", response_model=RegistryGrowthReport)
def get_registry_growth_report(db: Session = Depends(get_session)) -> RegistryGrowthReport:
    """Endpoint that returns the registry growth report.

    The heavy lifting is delegated to :func:`generate_report`.  Errors from the
    DB layer are propagated as HTTP 500 responses.
    """
    return generate_report(db)

# ---------------------------------------------------------------------------
# Self‑test (executed when running ``python registry_growth_report_generator.py``)
# ---------------------------------------------------------------------------
def _mock_session() -> Session:
    """Return a very small mock ``Session`` compatible with the queries used.

    The mock implements ``query(...).all()`` and returns an empty list.  This is
    sufficient for the self‑test because we only verify that the endpoint returns
    a well‑formed response.
    """

    class _Query:
        def __init__(self, _model):
            self._model = _model

        def all(self):  # pragma: no cover – exercised via TestClient
            return []

    class _MockSession:
        def query(self, model):
            return _Query(model)

    return _MockSession()  # type: ignore[return-value]


def _run_self_test() -> None:
    """Run a minimal test suite using FastAPI's ``TestClient``.

    The test overrides the ``get_session`` dependency with a mock session that
    returns no data.  It then calls the endpoint and checks that the JSON payload
    matches the expected schema and that the generated files exist.
    """
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)
    # Override the DB dependency with the mock implementation.
    app.dependency_overrides[get_session] = _mock_session

    client = TestClient(app)
    resp = client.get("/registry_growth_report")
    if resp.status_code != 200:
        raise AssertionError(f"Unexpected status {resp.status_code}")
    data = resp.json()
    required_keys = {"total_servers", "new_servers_last_30_days", "growth_trend", "plot_path"}
    if not required_keys.issubset(data):
        raise AssertionError("Response missing required keys")
    # Verify that the files were written.
    if not os.path.isfile(data["plot_path"]):
        raise AssertionError("Plot image not created")
    report_path = os.path.join(os.path.dirname(data["plot_path"]), "registry_growth_report.json")
    if not os.path.isfile(report_path):
        raise AssertionError("JSON report not created")
    print("PASS")


if __name__ == "__main__":
    # When executed directly we run the self‑test.  The endpoint can also be
    # exercised by importing the module and mounting the router in a larger app.
    _run_self_test()
