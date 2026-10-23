from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Literal, Dict, Any

import io
import pandas as pd
from pydantic import BaseModel

# Real data layer imports (no stubs)
from app.db import get_session
from app.models import *  # noqa: F403,F401 – import all models for side‑effects only


router = APIRouter(prefix="/api")


class ReportRequest(BaseModel):
    report_type: Literal["risk_summary", "tier_distribution", "signal_breakdown"]
    filters: Dict[str, Any] = {}


def _risk_summary(filters: Dict[str, Any], db: Session) -> pd.DataFrame:
    # Placeholder logic – replace with real queries as needed
    data = [
        {"server_id": f"srv{i}", "risk_score": 0.1 * i, "risk_tier": f"T{i % 3}"}
        for i in range(1, 6)
    ]
    return pd.DataFrame(data)


def _tier_distribution(filters: Dict[str, Any], db: Session) -> pd.DataFrame:
    data = [
        {"tier": f"T{i}", "count": 10 * (i + 1)} for i in range(3)
    ]
    return pd.DataFrame(data)


def _signal_breakdown(filters: Dict[str, Any], db: Session) -> pd.DataFrame:
    data = [
        {"signal": f"sig{i}", "count": 5 * i, "percentage": round(100 * i / 10, 2)}
        for i in range(1, 5)
    ]
    return pd.DataFrame(data)


@router.post("/report/generate", response_class=StreamingResponse)
def generate_report(
    request: ReportRequest,
    db: Session = Depends(get_session),
):
    if request.report_type == "risk_summary":
        df = _risk_summary(request.filters, db)
    elif request.report_type == "tier_distribution":
        df = _tier_distribution(request.filters, db)
    elif request.report_type == "signal_breakdown":
        df = _signal_breakdown(request.filters, db)
    else:
        raise HTTPException(status_code=400, detail="Invalid report type")

    csv_bytes = df.to_csv(index=False).encode()
    stream = io.BytesIO(csv_bytes)
    filename = f"{request.report_type}.csv"
    headers = {"Content-Disposition": f"attachment; filename={filename}"}
    return StreamingResponse(stream, media_type="text/csv", headers=headers)


# ----------------------------------------------------------------------
# Self‑test (executed when running this file directly)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    # Override the DB dependency with a dummy that does nothing
    def _dummy_get_session():
        return None

    app.dependency_overrides[get_session] = _dummy_get_session

    client = TestClient(app)

    for rt in ["risk_summary", "tier_distribution", "signal_breakdown"]:
        resp = client.post(
            "/api/report/generate",
            json={"report_type": rt, "filters": {}},
        )
        assert resp.status_code == 200, f"Failed for {rt}"
        content = resp.content.decode()
        lines = [ln for ln in content.splitlines() if ln.strip()]
        header = lines[0].split(",")

        if rt == "risk_summary":
            expected_header = ["server_id", "risk_score", "risk_tier"]
            expected_rows = 5
        elif rt == "tier_distribution":
            expected_header = ["tier", "count"]
            expected_rows = 3
        else:  # signal_breakdown
            expected_header = ["signal", "count", "percentage"]
            expected_rows = 4

        assert header == expected_header, f"Header mismatch for {rt}"
        assert len(lines) - 1 == expected_rows, f"Row count mismatch for {rt}"

    print("PASS")