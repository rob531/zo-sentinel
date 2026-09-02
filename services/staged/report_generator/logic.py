import pandas as pd
from io import StringIO
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import inspect

from app.db import get_session, Base
from app.models import McpServerRegistry  # type: ignore

router = APIRouter()


def _query_registry(session: Session, filters: Dict[str, Any]) -> List[McpServerRegistry]:
    """Return a list of McpServerRegistry rows matching the supplied filters."""
    query = session.query(McpServerRegistry)
    if filters:
        query = query.filter_by(**filters)
    return query.all()


def _registry_to_dataframe(rows: List[McpServerRegistry]) -> pd.DataFrame:
    """Convert a list of McpServerRegistry ORM objects to a pandas DataFrame."""
    cols = [c.key for c in inspect(McpServerRegistry).c]
    data = [{col: getattr(row, col) for col in cols} for row in rows]
    return pd.DataFrame(data)


def _risk_summary(session: Session, filters: Dict[str, Any]) -> pd.DataFrame:
    rows = _query_registry(session, filters)
    return _registry_to_dataframe(rows)


def _tier_distribution(session: Session, filters: Dict[str, Any]) -> pd.DataFrame:
    rows = _query_registry(session, filters)
    df = _registry_to_dataframe(rows)
    if "tier" not in df.columns:
        raise ValueError("Column 'tier' not found in McpServerRegistry")
    return df.groupby("tier").size().reset_index(name="count")


def _signal_breakdown(session: Session, filters: Dict[str, Any]) -> pd.DataFrame:
    rows = _query_registry(session, filters)
    df = _registry_to_dataframe(rows)
    if "signal_type" not in df.columns:
        raise ValueError("Column 'signal_type' not found in McpServerRegistry")
    return df.groupby("signal_type").size().reset_index(name="count")


def _generate_csv(df: pd.DataFrame) -> StreamingResponse:
    buffer = StringIO()
    df.to_csv(buffer, index=False)
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=report.csv"},
    )


@router.post("/api/report/generate")
async def generate_report(
    payload: Dict[str, Any],
    db: Session = Depends(get_session),
) -> StreamingResponse:
    report_type = payload.get("report_type")
    filters = payload.get("filters", {})

    if report_type == "risk_summary":
        df = _risk_summary(db, filters)
    elif report_type == "tier_distribution":
        df = _tier_distribution(db, filters)
    elif report_type == "signal_breakdown":
        df = _signal_breakdown(db, filters)
    else:
        raise ValueError(f"Unsupported report_type: {report_type}")

    return _generate_csv(df)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and override the app dependency
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(bind=engine)

    def get_test_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session
    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Populate minimal sample data required for the three report types
    # ------------------------------------------------------------------- #
    with SessionLocal() as db:
        # Insert rows with the columns used by the report logic
        db.add_all(
            [
                McpServerRegistry(
                    id=1,
                    name="srv‑one",
                    risk_score=7,
                    tier="high",
                    signal_type="auth",
                    signal_value=12,
                ),
                McpServerRegistry(
                    id=2,
                    name="srv‑two",
                    risk_score=3,
                    tier="low",
                    signal_type="network",
                    signal_value=5,
                ),
                McpServerRegistry(
                    id=3,
                    name="srv‑three",
                    risk_score=5,
                    tier="medium",
                    signal_type="auth",
                    signal_value=8,
                ),
            ]
        )
        db.commit()

    # ------------------------------------------------------------------- #
    # Execute a request for each report type and perform basic assertions
    # ------------------------------------------------------------------- #
    for rpt in ["risk_summary", "tier_distribution", "signal_breakdown"]:
        resp = client.post(
            "/api/report/generate",
            json={"report_type": rpt, "filters": {}},
        )
        if resp.status_code != 200:
            print(f"FAIL – {rpt} returned status {resp.status_code}", file=sys.stderr)
            sys.exit(1)

        csv_text = resp.text.strip()
        lines = csv_text.splitlines()
        if len(lines) < 2:
            print(f"FAIL – {rpt} CSV has insufficient rows", file=sys.stderr)
            sys.exit(1)

        header = lines[0].split(",")
        # Basic column checks per report type
        if rpt == "risk_summary":
            expected_cols = {"id", "name", "risk_score", "tier", "signal_type", "signal_value"}
        elif rpt == "tier_distribution":
            expected_cols = {"tier", "count"}
        else:  # signal_breakdown
            expected_cols = {"signal_type", "count"}

        if not expected_cols.issubset(set(header)):
            print(
                f"FAIL – {rpt} CSV missing expected columns. Got {header}",
                file=sys.stderr,
            )
            sys.exit(1)

    print("PASS")