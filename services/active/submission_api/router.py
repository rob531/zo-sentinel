# deps: fastapi, pydantic, sqlalchemy, pyjwt
"""MCP Server Submission API.

Public endpoints for listing, creating, and retrieving MCP server submissions.
Data lives in mcp_submissions (managed by the submission pipeline) joined against
mcp_server_registry for trust/risk metadata.

Auth: public (auth=public in the directive).
Data: app DB via get_session + SQLAlchemy ORM on McpServerRegistry; raw SQL for
mcp_submissions (pipeline-managed table, not an ORM model).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api/submissions", tags=["submission_api"])


# --------------------------------------------------------------------------- #
# Pydantic request/response models
# --------------------------------------------------------------------------- #


class ServerMeta(BaseModel):
    trust_score: Optional[float] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None


class SubmissionResponse(BaseModel):
    id: int
    mcp_name: str
    mcp_identifier: str
    registry_source: str
    requested_by: str
    org_id: int
    status: str
    created_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    review_decision: Optional[str] = None
    review_conditions: Optional[str] = None
    server_metadata: ServerMeta = ServerMeta()

    class Config:
        from_attributes = True


class SubmissionListResponse(BaseModel):
    items: list[SubmissionResponse]
    total: int
    page: int
    page_size: int


# --------------------------------------------------------------------------- #
# Helper: resolve trust/risk metadata from McpServerRegistry ORM
# --------------------------------------------------------------------------- #


def _server_meta(
    mcp_identifier: str,
    registry_source: str,
    session: Session,
) -> ServerMeta:
    server = (
        session.query(McpServerRegistry)
        .filter(
            McpServerRegistry.name == mcp_identifier,
            McpServerRegistry.registry_source == registry_source,
        )
        .first()
    )
    if server:
        return ServerMeta(
            trust_score=server.trust_score,
            risk_tier=server.risk_tier,
            verdict=server.verdict,
        )
    return ServerMeta()


def _row_to_response(row, session: Session) -> SubmissionResponse:
    return SubmissionResponse(
        id=row.id,
        mcp_name=row.mcp_name,
        mcp_identifier=row.mcp_identifier,
        registry_source=row.registry_source,
        requested_by=row.requested_by,
        org_id=row.org_id,
        status=row.status,
        created_at=row.created_at,
        reviewed_at=row.reviewed_at,
        reviewed_by=row.reviewed_by,
        review_decision=row.review_decision,
        review_conditions=row.review_conditions,
        server_metadata=_server_meta(row.mcp_identifier, row.registry_source, session),
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get("/", response_model=SubmissionListResponse)
def list_submissions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="Filter by submission status"),
    org_id: Optional[int] = Query(None, description="Filter by org_id"),
    session: Session = Depends(get_session),
) -> SubmissionListResponse:
    offset = (page - 1) * page_size

    count_sql = text("""
        SELECT COUNT(*)
        FROM mcp_submissions s
        WHERE (:status IS NULL OR s.status = :status)
          AND (:org_id IS NULL OR s.org_id = :org_id)
    """)
    total = session.execute(count_sql, {"status": status, "org_id": org_id}).scalar() or 0

    list_sql = text("""
        SELECT
            s.id, s.mcp_name, s.mcp_identifier, s.registry_source,
            s.requested_by, s.org_id, s.status, s.created_at,
            s.reviewed_at, s.reviewed_by, s.review_decision, s.review_conditions
        FROM mcp_submissions s
        WHERE (:status IS NULL OR s.status = :status)
          AND (:org_id IS NULL OR s.org_id = :org_id)
        ORDER BY s.created_at DESC
        LIMIT :limit OFFSET :offset
    """)
    rows = session.execute(
        list_sql,
        {"status": status, "org_id": org_id, "limit": page_size, "offset": offset},
    ).fetchall()

    items = [_row_to_response(row, session) for row in rows]
    return SubmissionListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/", response_model=SubmissionResponse, status_code=201)
def create_submission(
    mcp_name: str = Query(...),
    mcp_identifier: str = Query(...),
    registry_source: str = Query(...),
    requested_by: str = Query(...),
    org_id: int = Query(...),
    session: Session = Depends(get_session),
) -> SubmissionResponse:
    now = datetime.utcnow()
    insert_sql = text("""
        INSERT INTO mcp_submissions
            (mcp_name, mcp_identifier, registry_source, requested_by, org_id, status, created_at)
        VALUES
            (:mcp_name, :mcp_identifier, :registry_source, :requested_by, :org_id, 'pending', :created_at)
        RETURNING
            id, mcp_name, mcp_identifier, registry_source, requested_by,
            org_id, status, created_at, reviewed_at, reviewed_by, review_decision, review_conditions
    """)
    result = session.execute(
        insert_sql,
        {
            "mcp_name": mcp_name,
            "mcp_identifier": mcp_identifier,
            "registry_source": registry_source,
            "requested_by": requested_by,
            "org_id": org_id,
            "created_at": now,
        },
    )
    session.commit()
    row = result.fetchone()
    return _row_to_response(row, session)


@router.get("/{submission_id}", response_model=SubmissionResponse)
def get_submission(
    submission_id: int,
    session: Session = Depends(get_session),
) -> SubmissionResponse:
    sql = text("""
        SELECT
            id, mcp_name, mcp_identifier, registry_source, requested_by,
            org_id, status, created_at, reviewed_at, reviewed_by, review_decision, review_conditions
        FROM mcp_submissions
        WHERE id = :id
    """)
    row = session.execute(sql, {"id": submission_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    return _row_to_response(row, session)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    with test_engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE mcp_submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mcp_name TEXT NOT NULL,
                    mcp_identifier TEXT NOT NULL,
                    registry_source TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    org_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMP NOT NULL,
                    reviewed_at TIMESTAMP,
                    reviewed_by TEXT,
                    review_decision TEXT,
                    review_conditions TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    registry_source TEXT,
                    trust_score REAL,
                    risk_tier TEXT,
                    verdict TEXT,
                    confidence REAL,
                    description TEXT,
                    first_seen TIMESTAMP,
                    last_assessed TIMESTAMP,
                    last_scanned TIMESTAMP,
                    last_seen TIMESTAMP,
                    scan_count INTEGER DEFAULT 0,
                    meta TEXT,
                    url TEXT,
                    verdict_reasoning TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO mcp_submissions
                    (mcp_name, mcp_identifier, registry_source, requested_by, org_id, status, created_at)
                VALUES
                    ('Alpha Server', 'server-alpha', 'npm', 'user1', 1, 'pending', CURRENT_TIMESTAMP),
                    ('Beta Server', 'server-beta', 'github', 'user2', 1, 'approved', CURRENT_TIMESTAMP)
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, registry_source, trust_score, risk_tier, verdict)
                VALUES
                    ('srv-alpha', 'server-alpha', 'npm', 0.85, 'low', 'trusted'),
                    ('srv-beta', 'server-beta', 'github', 0.72, 'medium', 'caution')
                """
            )
        )

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    # GET /api/submissions
    resp = client.get("/api/submissions")
    assert resp.status_code == 200, f"GET / failed: {resp.status_code}"
    data = resp.json()
    assert data["total"] >= 2, f"Expected >=2, got {data['total']}"
    assert len(data["items"]) >= 2
    assert data["items"][0]["server_metadata"]["trust_score"] is not None

    # POST /api/submissions
    resp = client.post(
        "/api/submissions",
        params={
            "mcp_name": "Gamma Server",
            "mcp_identifier": "server-gamma",
            "registry_source": "npm",
            "requested_by": "user3",
            "org_id": 1,
        },
    )
    assert resp.status_code == 201, f"POST / failed: {resp.status_code}"
    created = resp.json()
    assert created["mcp_name"] == "Gamma Server"
    assert created["status"] == "pending"

    # GET /api/submissions/{id}
    resp = client.get(f"/api/submissions/{created['id']}")
    assert resp.status_code == 200, f"GET /{{id}} failed: {resp.status_code}"
    fetched = resp.json()
    assert fetched["id"] == created["id"]

    # 404 for unknown id
    resp = client.get("/api/submissions/99999")
    assert resp.status_code == 404

    print("PASS")
