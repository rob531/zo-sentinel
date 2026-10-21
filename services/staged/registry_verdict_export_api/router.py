from __future__ import annotations

import csv
import inspect
import io
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import export_registry_verdicts as _select_registry_verdicts
except ImportError:
    try:
        from .logic import get_registry_verdicts as _select_registry_verdicts
    except ImportError:
        try:
            from .logic import fetch_registry_verdicts as _select_registry_verdicts
        except ImportError:
            from .logic import select_registry_verdicts as _select_registry_verdicts


router = APIRouter(prefix="/api", tags=["registry-export"])

EXPORT_COLUMNS = [
    "server_id",
    "name",
    "registry_source",
    "url",
    "trust_score",
    "verdict",
    "risk_tier",
    "confidence",
    "last_assessed",
    "scan_count",
]


class RegistryExportRow(BaseModel):
    server_id: Optional[str] = None
    name: Optional[str] = None
    registry_source: Optional[str] = None
    url: Optional[str] = None
    trust_score: Optional[float] = None
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None
    confidence: Optional[float] = None
    last_assessed: Optional[datetime] = None
    scan_count: Optional[int] = None


class RegistryExportResponse(BaseModel):
    filters: dict[str, Optional[str]]
    count: int
    rows: list[RegistryExportRow]
    columns: list[str]


def _run_selector(
    session: Session,
    risk_tier: Optional[str],
    source: Optional[str],
    limit: int,
) -> Any:
    signature = inspect.signature(_select_registry_verdicts)
    parameters = signature.parameters
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )

    if accepts_kwargs:
        kwargs: dict[str, Any] = {
            "risk_tier": risk_tier,
            "source": source,
            "registry_source": source,
            "limit": limit,
        }
        session_name = next(
            (
                name
                for name in ("session", "db")
                if name in parameters
            ),
            None,
        )
        if session_name is not None:
            kwargs[session_name] = session
            return _select_registry_verdicts(**kwargs)

    kwargs = {}
    if "risk_tier" in parameters:
        kwargs["risk_tier"] = risk_tier
    if "source" in parameters:
        kwargs["source"] = source
    elif "registry_source" in parameters:
        kwargs["registry_source"] = source
    if "limit" in parameters:
        kwargs["limit"] = limit

    session_name = next(
        (
            name
            for name in ("session", "db")
            if name in parameters
        ),
        None,
    )
    if session_name is not None:
        kwargs[session_name] = session
        return _select_registry_verdicts(**kwargs)

    positional = [
        parameter
        for parameter in parameters.values()
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    if positional:
        return _select_registry_verdicts(session, **kwargs)
    return _select_registry_verdicts(**kwargs)


def _normalize_rows(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, Mapping):
        if "rows" in result:
            result = result["rows"]
        elif "server_id" in result:
            result = [result]
        else:
            result = []

    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], list):
        result = result[0]

    normalized: list[dict[str, Any]] = []
    for row in result or []:
        if isinstance(row, Mapping):
            values = row
        elif hasattr(row, "_mapping"):
            values = row._mapping
        elif hasattr(row, "keys") and hasattr(row, "__getitem__"):
            values = {column: row[column] for column in EXPORT_COLUMNS}
        elif isinstance(row, (tuple, list)):
            values = dict(zip(EXPORT_COLUMNS, row))
        else:
            values = {
                column: getattr(row, column, None)
                for column in EXPORT_COLUMNS
            }
        normalized.append(
            {column: values.get(column) for column in EXPORT_COLUMNS}
        )
    return normalized


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    return (
        str(value)
        .replace("\r\n", " ")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _csv_response(rows: list[dict[str, Any]]) -> StreamingResponse:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow([_csv_value(row.get(column)) for column in EXPORT_COLUMNS])

    def stream():
        yield output.getvalue()

    return StreamingResponse(
        stream(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="registry_verdicts.csv"'
        },
    )


@router.get(
    "/registry/export",
    response_model=RegistryExportResponse,
)
def export_registry(
    risk_tier: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    format: str = Query(default="json", pattern="^(json|csv)$"),
    session: Session = Depends(get_session),
):
    result = _run_selector(session, risk_tier, source, limit)
    rows = _normalize_rows(result)

    if format == "csv":
        return _csv_response(rows)

    return {
        "filters": {
            "risk_tier": risk_tier,
            "source": source,
        },
        "count": len(rows),
        "rows": rows,
        "columns": EXPORT_COLUMNS,
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestSession() as test_session:
        test_session.add_all(
            [
                McpServerRegistry(
                    server_id="registry-1",
                    name="Registry One",
                    registry_source="community",
                    url="https://example.com/one",
                    trust_score=20.0,
                    verdict="approved",
                    verdict_reasoning="Low risk",
                    confidence=0.9,
                    risk_tier="low",
                    last_assessed=datetime(2025, 1, 1),
                    first_seen=datetime(2025, 1, 1),
                    last_seen=datetime(2025, 1, 1),
                    scan_count=2,
                ),
                McpServerRegistry(
                    server_id="registry-2",
                    name="Registry Two",
                    registry_source="official",
                    url="https://example.com/two",
                    trust_score=40.0,
                    verdict="conditional",
                    verdict_reasoning="Review required",
                    confidence=0.8,
                    risk_tier="high",
                    last_assessed=datetime(2025, 1, 2),
                    first_seen=datetime(2025, 1, 2),
                    last_seen=datetime(2025, 1, 2),
                    scan_count=3,
                ),
                McpServerRegistry(
                    server_id="registry-3",
                    name="Registry Three",
                    registry_source="community",
                    url="https://example.com/three",
                    trust_score=60.0,
                    verdict="rejected",
                    verdict_reasoning="High risk",
                    confidence=0.7,
                    risk_tier="high",
                    last_assessed=datetime(2025, 1, 3),
                    first_seen=datetime(2025, 1, 3),
                    last_seen=datetime(2025, 1, 3),
                    scan_count=4,
                ),
                McpServerRegistry(
                    server_id="registry-4",
                    name="Registry Four",
                    registry_source="official",
                    url="https://example.com/four",
                    trust_score=80.0,
                    verdict="approved",
                    verdict_reasoning="Low risk",
                    confidence=0.95,
                    risk_tier="low",
                    last_assessed=datetime(2025, 1, 4),
                    first_seen=datetime(2025, 1, 4),
                    last_seen=datetime(2025, 1, 4),
                    scan_count=5,
                ),
            ]
        )
        test_session.commit()

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        json_response = client.get("/api/registry/export")
        assert json_response.status_code == 200
        assert json_response.json()["count"] == 4

        filtered_response = client.get(
            "/api/registry/export", params={"risk_tier": "high"}
        )
        assert filtered_response.status_code == 200
        assert filtered_response.json()["count"] == 2
        assert all(
            row["risk_tier"] == "high"
            for row in filtered_response.json()["rows"]
        )

        csv_response = client.get(
            "/api/registry/export", params={"format": "csv"}
        )
        assert csv_response.status_code == 200
        assert csv_response.headers["content-type"].startswith("text/csv")
        assert csv_response.text.splitlines()[0] == ",".join(EXPORT_COLUMNS)

    print("PASS")