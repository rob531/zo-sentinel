from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import list_exemptions as _logic_list_exemptions
except Exception:
    _logic_list_exemptions = None

try:
    from . import logic as _logic_module
except Exception:
    _logic_module = None


router = APIRouter(prefix="/api", tags=["exemptions"])


class ExemptionItem(BaseModel):
    server_id: Any = None
    name: Any = None
    risk_tier: Any = None
    verdict: Any = None
    reason: Any = None
    granted_by: Any = None
    granted_at: Any = None
    expires_at: Any = None
    status: Any = None
    days_to_expiry: int


class ExemptionListResponse(BaseModel):
    count: int
    exemptions: List[ExemptionItem]


def _to_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def _to_response_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _days_to_expiry(value: Any) -> int:
    expiry = _to_datetime(value)
    if expiry is None:
        return 0
    now = datetime.now(timezone.utc) if expiry.tzinfo is not None else datetime.utcnow()
    return int((expiry - now).days)


@router.get("/exemptions", response_model=ExemptionListResponse)
def get_exemptions(
    status: str = Query("all", pattern="^(active|expired|all)$"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_session),
) -> ExemptionListResponse:
    sql = text(
        """
        SELECT e.server_id AS server_id,
               e.reason AS reason,
               e.granted_by AS granted_by,
               e.granted_at AS granted_at,
               e.expires_at AS expires_at,
               e.status AS status,
               r.name AS name,
               r.risk_tier AS risk_tier,
               r.verdict AS verdict
        FROM mcp_exemptions AS e
        LEFT JOIN mcp_server_registry AS r
               ON r.server_id = e.server_id
        WHERE (:status = 'all' OR e.status = :status)
        ORDER BY e.granted_at DESC
        LIMIT :limit
        """
    )
    rows = db.execute(sql, {"status": status, "limit": limit}).mappings().all()

    items: List[ExemptionItem] = []
    for row in rows:
        items.append(
            ExemptionItem(
                server_id=_to_response_value(row.get("server_id")),
                name=_to_response_value(row.get("name")),
                risk_tier=_to_response_value(row.get("risk_tier")),
                verdict=_to_response_value(row.get("verdict")),
                reason=_to_response_value(row.get("reason")),
                granted_by=_to_response_value(row.get("granted_by")),
                granted_at=_to_response_value(row.get("granted_at")),
                expires_at=_to_response_value(row.get("expires_at")),
                status=_to_response_value(row.get("status")),
                days_to_expiry=_days_to_expiry(row.get("expires_at")),
            )
        )

    return ExemptionListResponse(count=len(items), exemptions=items)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE mcp_exemptions (
                    server_id TEXT,
                    reason TEXT,
                    granted_by TEXT,
                    granted_at TEXT,
                    expires_at TEXT,
                    status TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT,
                    name TEXT,
                    risk_tier TEXT,
                    verdict TEXT
                )
                """
            )
        )

    now = datetime.utcnow()
    seed_rows = [
        (
            "srv-active",
            "active exemption",
            "admin",
            (now - __import__("datetime").timedelta(days=2)).isoformat(),
            (now + __import__("datetime").timedelta(days=30)).isoformat(),
            "active",
            "Active Server",
            "low",
            "allow",
        ),
        (
            "srv-expired",
            "expired exemption",
            "admin",
            (now - __import__("datetime").timedelta(days=20)).isoformat(),
            (now - __import__("datetime").timedelta(days=10)).isoformat(),
            "expired",
            "Expired Server",
            "high",
            "deny",
        ),
        (
            "srv-expiring",
            "expiring exemption",
            "admin",
            (now - __import__("datetime").timedelta(days=1)).isoformat(),
            (now + __import__("datetime").timedelta(days=3)).isoformat(),
            "active",
            "Expiring Server",
            "medium",
            "review",
        ),
    ]

    with engine.begin() as conn:
        for row in seed_rows:
            conn.execute(
                text(
                    """
                    INSERT INTO mcp_exemptions
                        (server_id, reason, granted_by, granted_at, expires_at, status)
                    VALUES
                        (:server_id, :reason, :granted_by, :granted_at, :expires_at, :status)
                    """
                ),
                {
                    "server_id": row[0],
                    "reason": row[1],
                    "granted_by": row[2],
                    "granted_at": row[3],
                    "expires_at": row[4],
                    "status": row[5],
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry
                        (server_id, name, risk_tier, verdict)
                    VALUES
                        (:server_id, :name, :risk_tier, :verdict)
                    """
                ),
                {
                    "server_id": row[0],
                    "name": row[6],
                    "risk_tier": row[7],
                    "verdict": row[8],
                },
            )

    SessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)
    response = client.get("/api/exemptions")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["count"] == 3, data

    active_items = [item for item in data["exemptions"] if item["status"] == "active"]
    assert active_items, data
    active_item = active_items[0]
    assert active_item["status"] == "active"
    assert active_item["risk_tier"] is not None

    for item in data["exemptions"]:
        assert isinstance(item["days_to_expiry"], int), item

    print("PASS")