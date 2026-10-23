from __future__ import annotations

import csv
import io
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Optional

project_root = str(Path(__file__).resolve().parents[3])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["search"])

CSV_COLUMNS = [
    "server_id",
    "name",
    "url",
    "registry_source",
    "risk_tier",
    "verdict",
    "trust_score",
    "last_scanned",
    "overall_risk_p_top",
    "description",
]


def _latest_overall_risk_scores():
    return (
        select(
            McpLlmAxisScore.server_id.label("server_id"),
            McpLlmAxisScore.p_top.label("overall_risk_p_top"),
            func.row_number()
            .over(
                partition_by=McpLlmAxisScore.server_id,
                order_by=(
                    McpLlmAxisScore.scored_at.is_(None).asc(),
                    McpLlmAxisScore.scored_at.desc(),
                    McpLlmAxisScore.id.desc(),
                ),
            )
            .label("score_rank"),
        )
        .where(McpLlmAxisScore.axis_name == "overall_risk")
        .subquery()
    )


def _build_search_query(
    session: Session,
    q: Optional[str] = None,
    risk_tier: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    row_limit = min(max(int(limit), 1), 5000)
    latest_scores = _latest_overall_risk_scores()
    statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.url,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
            McpServerRegistry.trust_score,
            McpServerRegistry.last_scanned,
            latest_scores.c.overall_risk_p_top,
            McpServerRegistry.description,
        )
        .outerjoin(
            latest_scores,
            and_(
                latest_scores.c.server_id == McpServerRegistry.server_id,
                latest_scores.c.score_rank == 1,
            ),
        )
    )

    filters = []
    if q and q.strip():
        search_text = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        search_pattern = f"%{search_text}%"
        filters.append(
            or_(
                McpServerRegistry.name.ilike(search_pattern, escape="\\"),
                McpServerRegistry.description.ilike(search_pattern, escape="\\"),
            )
        )
    if risk_tier:
        filters.append(McpServerRegistry.risk_tier == risk_tier)
    if source:
        filters.append(McpServerRegistry.registry_source == source)
    if filters:
        statement = statement.where(*filters)

    rows = session.execute(
        statement.order_by(McpServerRegistry.server_id).limit(row_limit)
    ).mappings().all()

    results: list[dict[str, Any]] = []
    for row in rows:
        scanned_at = row["last_scanned"]
        if scanned_at is not None and hasattr(scanned_at, "isoformat"):
            scanned_at = scanned_at.isoformat()
        results.append(
            {
                "server_id": row["server_id"],
                "name": row["name"],
                "url": row["url"],
                "registry_source": row["registry_source"],
                "risk_tier": row["risk_tier"],
                "verdict": row["verdict"],
                "trust_score": row["trust_score"],
                "last_scanned": scanned_at or "",
                "overall_risk_p_top": row["overall_risk_p_top"]
                if row["overall_risk_p_top"] is not None
                else "",
                "description": row["description"] or "",
            }
        )
    return results


def generate_export_csv(
    session: Session,
    q: Optional[str] = None,
    risk_tier: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 500,
) -> StreamingResponse:
    results = _build_search_query(session, q, risk_tier, source, limit)

    def stream_csv() -> Iterator[str]:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        for row in results:
            writer.writerow(row)
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    return StreamingResponse(
        stream_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=server_search_export.csv"},
    )


def get_search_export(
    session: Session,
    q: Optional[str] = None,
    risk_tier: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 500,
) -> StreamingResponse:
    return generate_export_csv(session, q, risk_tier, source, limit)


@router.get("/search/export")
def export_search(
    q: Optional[str] = Query(None, description="Search query matching server name or description"),
    risk_tier: Optional[str] = Query(None, description="Filter by risk tier"),
    source: Optional[str] = Query(None, description="Filter by registry source"),
    limit: int = Query(500, ge=1, le=5000, description="Maximum rows; defaults to 500 and is capped at 5000"),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    return generate_export_csv(session, q, risk_tier, source, limit)


def run_self_test() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[McpServerRegistry.__table__, McpLlmAxisScore.__table__],
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    risk_tiers = [
        "LOW_RISK_STANDARD",
        "MEDIUM_RISK_MONITORED",
        "HIGH_RISK_ISOLATED",
        "HIGH_RISK_CONNECTED",
        "HIGH_RISK_ISOLATED",
        "TRUSTED",
        "MEDIUM_RISK_MONITORED",
        "LOW_RISK_STANDARD",
        "HIGH_RISK_ISOLATED",
        "HIGH_RISK_ISOLATED",
    ]
    sources = ["community", "enterprise", "verified", "internal"]
    expected_server_ids = {
        f"srv-{index + 1:03d}"
        for index, tier in enumerate(risk_tiers)
        if tier == "HIGH_RISK_ISOLATED"
    }

    with testing_session() as session:
        for index, tier in enumerate(risk_tiers):
            server_id = f"srv-{index + 1:03d}"
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=f"Server {index + 1:03d}",
                    url=f"https://server-{index + 1:03d}.example.test",
                    registry_source=sources[index % len(sources)],
                    risk_tier=tier,
                    verdict="REVIEW_REQUIRED" if "HIGH_RISK" in tier else "APPROVED",
                    trust_score=0.25 + index * 0.05,
                    last_scanned=datetime(2025, 1, 1) + timedelta(days=index),
                    description=f"Security audit server {index + 1:03d}",
                )
            )
            session.add(
                McpLlmAxisScore(
                    id=index + 1,
                    server_id=server_id,
                    axis_name="overall_risk",
                    label="HIGH" if "HIGH_RISK" in tier else "LOW",
                    label_index=1 if "HIGH_RISK" in tier else 0,
                    p_top=0.9 if "HIGH_RISK" in tier else 0.1,
                    model_version="server-search-export-self-test-v1",
                    scored_at=datetime(2025, 1, 1) + timedelta(days=index),
                )
            )
        session.add(
            McpLlmAxisScore(
                id=101,
                server_id="srv-003",
                axis_name="overall_risk",
                label="HIGH",
                label_index=1,
                p_top=0.93,
                model_version="server-search-export-self-test-v2",
                scored_at=datetime(2025, 2, 1),
            )
        )
        session.commit()

    def override_get_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get(
                "/api/search/export",
                params={"risk_tier": "HIGH_RISK_ISOLATED"},
            )
            query_response = client.get(
                "/api/search/export",
                params={"q": "audit server 003", "source": "verified"},
            )

        assert response.status_code == 200, response.text
        assert response.headers.get("content-type", "").startswith("text/csv"), response.headers
        assert "attachment" in response.headers.get("content-disposition", ""), response.headers

        reader = csv.DictReader(io.StringIO(response.text))
        rows = list(reader)
        assert reader.fieldnames is not None
        assert set(CSV_COLUMNS).issubset(reader.fieldnames), reader.fieldnames
        assert len(rows) == len(expected_server_ids), rows
        assert {row["server_id"] for row in rows} == expected_server_ids, rows
        assert all(row["risk_tier"] == "HIGH_RISK_ISOLATED" for row in rows), rows
        assert all(
            row["overall_risk_p_top"] == ("0.93" if row["server_id"] == "srv-003" else "0.9")
            for row in rows
        ), rows

        assert query_response.status_code == 200, query_response.text
        query_rows = list(csv.DictReader(io.StringIO(query_response.text)))
        assert [row["server_id"] for row in query_rows] == ["srv-003"], query_rows
    finally:
        engine.dispose()

    return True


if __name__ == "__main__":
    try:
        run_self_test()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")