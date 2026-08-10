"""
services/staged/verdict_watchlist/contract.py

FastAPI contract for the verdict watchlist service.
Mirrors the exemplar contract and provides a self‑test runnable via:
    python -m services.staged.verdict_watchlist.contract
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

# Real application dependencies – must be imported exactly as in the app.
from app.db import get_session
from app.dependencies import get_current_user  # assumed location
# Trust gating – imported for completeness; not used in the self‑test.
from trust_gating_override import trust_gate  # type: ignore

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class WatchlistItem(BaseModel):
    server_id: int
    name: str
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    last_assessed: Optional[datetime] = None


class WatchlistResponse(BaseModel):
    items: List[WatchlistItem]


class WatchlistAddRequest(BaseModel):
    server_id: int


class RiskDetailAxis(BaseModel):
    axis: str
    score: float


class RiskDetailResponse(BaseModel):
    server_id: int
    axes: List[RiskDetailAxis]
    trust_override_verdict: Optional[str] = None


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #
def _org_id_from_user(user) -> int:
    """Extract org_id from the authenticated user."""
    return getattr(user, "org_id", None)


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get("/", response_model=WatchlistResponse)
def get_watchlist(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_session),
):
    org_id = _org_id_from_user(current_user)
    if org_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Org ID missing")

    sql = """
        SELECT
            w.server_id,
            s.name,
            s.risk_tier,
            s.verdict,
            s.last_assessed
        FROM mcp_watchlist w
        JOIN McpServerRegistry s ON w.server_id = s.id
        WHERE w.org_id = :org_id
    """
    rows = db.execute(text(sql), {"org_id": org_id}).fetchall()
    items = [
        WatchlistItem(
            server_id=row["server_id"],
            name=row["name"],
            risk_tier=row["risk_tier"],
            verdict=row["verdict"],
            last_assessed=row["last_assessed"],
        )
        for row in rows
    ]
    return WatchlistResponse(items=items)


@router.post("/", status_code=status.HTTP_201_CREATED)
def add_to_watchlist(
    payload: WatchlistAddRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_session),
):
    org_id = _org_id_from_user(current_user)
    if org_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Org ID missing")

    sql = """
        INSERT INTO mcp_watchlist (server_id, org_id, added_by, added_at)
        VALUES (:server_id, :org_id, :added_by, :added_at)
        ON CONFLICT(server_id, org_id) DO UPDATE SET
            added_by = excluded.added_by,
            added_at = excluded.added_at
    """
    db.execute(
        text(sql),
        {
            "server_id": payload.server_id,
            "org_id": org_id,
            "added_by": getattr(current_user, "sub", None),
            "added_at": datetime.utcnow(),
        },
    )
    db.commit()
    return {"detail": "added"}


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_from_watchlist(
    server_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_session),
):
    org_id = _org_id_from_user(current_user)
    if org_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Org ID missing")

    sql = """
        DELETE FROM mcp_watchlist
        WHERE server_id = :server_id AND org_id = :org_id
    """
    db.execute(text(sql), {"server_id": server_id, "org_id": org_id})
    db.commit()
    return


@router.get("/{server_id}/risk_detail", response_model=RiskDetailResponse)
def get_risk_detail(
    server_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_session),
):
    org_id = _org_id_from_user(current_user)
    if org_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Org ID missing")

    # Axis scores
    axis_sql = """
        SELECT axis, score
        FROM McpLlmAxisScore
        WHERE server_id = :server_id
    """
    axis_rows = db.execute(text(axis_sql), {"server_id": server_id}).fetchall()
    axes = [RiskDetailAxis(axis=row["axis"], score=row["score"]) for row in axis_rows]

    # Trust override verdict (if any)
    try:
        trust_verdict = trust_gate(server_id)  # placeholder call
    except Exception:
        trust_verdict = None

    return RiskDetailResponse(
        server_id=server_id,
        axes=axes,
        trust_override_verdict=trust_verdict,
    )


# --------------------------------------------------------------------------- #
# Self‑test (run as a module)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Build a minimal in‑memory SQLite DB for the self‑test
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    with engine.begin() as conn:
        conn.executescript(
            """
            CREATE TABLE McpServerRegistry (
                id INTEGER PRIMARY KEY,
                org_id INTEGER,
                name TEXT,
                risk_tier TEXT,
                verdict TEXT,
                last_assessed TEXT
            );
            CREATE TABLE mcp_watchlist (
                server_id INTEGER,
                org_id INTEGER,
                added_by INTEGER,
                added_at TEXT,
                PRIMARY KEY (server_id, org_id)
            );
            """
        )
        # Seed two servers belonging to org 100
        conn.execute(
            text(
                """
                INSERT INTO McpServerRegistry (id, org_id, name, risk_tier, verdict, last_assessed)
                VALUES
                    (1, 100, 'Alpha', 'high', 'malicious', '2024-01-01T00:00:00Z'),
                    (2, 100, 'Beta',  'medium', 'suspicious', '2024-01-02T00:00:00Z');
                """
            )
        )

    # ------------------------------------------------------------------- #
    # FastAPI app wiring
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # Override dependencies
    def get_test_session() -> Session:
        return SessionLocal()

    class TestUser:
        org_id = 100
        sub = 999

    app.dependency_overrides[get_session] = get_test_session
    app.dependency_overrides[get_current_user] = lambda: TestUser()

    # ------------------------------------------------------------------- #
    # Run the acceptance test
    # ------------------------------------------------------------------- #
    client = TestClient(app)

    resp = client.get("/api/watchlist")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}")
        sys.exit(1)

    payload = resp.json()
    if not isinstance(payload, dict) or "items" not in payload or not payload["items"]:
        print("FAIL: payload malformed or empty")
        sys.exit(1)

    if not any("server_id" in i and "risk_tier" in i for i in payload["items"]):
        print("FAIL: missing expected fields")
        sys.exit(1)

    print("PASS")
    sys.exit(0)