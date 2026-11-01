from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org
from .logic import get_rotation_due_report

router = APIRouter(prefix="/api")


class OrgKeyRow(BaseModel):
    org_id: str
    org_name: str
    key_count: int
    oldest_key_created_at: str
    max_age_days: int
    keys_due: int


class RotationDueReport(BaseModel):
    total_orgs: int
    stale_days_threshold: int
    orgs: list[OrgKeyRow]


def _normalise_report(report: Any, stale_days: int) -> dict[str, Any]:
    if hasattr(report, "model_dump"):
        report = report.model_dump()
    elif isinstance(report, tuple) and len(report) == 2:
        report = {"orgs": report[0], "stale_days_threshold": report[1]}

    if isinstance(report, list):
        report = {"orgs": report}

    rows = report.get("orgs", []) if isinstance(report, dict) else []
    normalised_rows: list[dict[str, Any]] = []

    for row in rows:
        if hasattr(row, "model_dump"):
            row = row.model_dump()
        elif not isinstance(row, dict):
            row = {
                "org_id": getattr(row, "org_id", ""),
                "org_name": getattr(row, "org_name", ""),
                "key_count": getattr(row, "key_count", 0),
                "oldest_key_created_at": getattr(row, "oldest_key_created_at", ""),
                "max_age_days": getattr(row, "max_age_days", 0),
                "keys_due": getattr(row, "keys_due", 0),
            }

        oldest = row.get("oldest_key_created_at")
        if isinstance(oldest, datetime):
            oldest = oldest.isoformat()

        normalised_rows.append(
            {
                "org_id": str(row.get("org_id", "")),
                "org_name": str(row.get("org_name", "")),
                "key_count": int(row.get("key_count", 0)),
                "oldest_key_created_at": str(oldest or ""),
                "max_age_days": int(row.get("max_age_days", 0)),
                "keys_due": int(row.get("keys_due", 0)),
            }
        )

    normalised_rows.sort(key=lambda item: item["max_age_days"], reverse=True)
    return {
        "total_orgs": len(normalised_rows),
        "stale_days_threshold": stale_days,
        "orgs": normalised_rows,
    }


@router.get("/api-keys/rotation-due", response_model=RotationDueReport)
def api_key_rotation_due(
    stale_days: int = Query(default=90, ge=1),
    org_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    report = get_rotation_due_report(session, stale_days, org_id)
    return _normalise_report(report, stale_days)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(test_engine)
    ApiKey.__table__.create(test_engine)
    TestingSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_session = TestingSession()

    now = datetime.utcnow()
    test_session.add_all(
        [
            Org(id="org-old", name="Old Credentials", created_at=now - timedelta(days=200)),
            Org(id="org-new", name="Current Credentials", created_at=now - timedelta(days=30)),
            ApiKey(
                id="key-old",
                org_id="org-old",
                key_hash="old-secret-hash-value",
                label="old",
                created_at=now - timedelta(days=120),
            ),
            ApiKey(
                id="key-recent-old-org",
                org_id="org-old",
                key_hash="recent-secret-hash-value",
                label="recent",
                created_at=now - timedelta(days=10),
            ),
            ApiKey(
                id="key-new",
                org_id="org-new",
                key_hash="new-secret-hash-value",
                label="new",
                created_at=now - timedelta(days=5),
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: test_session

    response = TestClient(test_app).get(
        "/api/api-keys/rotation-due",
        params={"stale_days": 90},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["stale_days_threshold"] == 90
    assert payload["total_orgs"] == len(payload["orgs"])
    assert payload["orgs"]
    assert payload["orgs"][0]["org_id"] == "org-old"
    assert payload["orgs"][0]["keys_due"] >= 1
    assert all("key_hash" not in row for row in payload["orgs"])
    assert "old-secret-hash-value" not in response.text
    assert "recent-secret-hash-value" not in response.text
    assert "new-secret-hash-value" not in response.text

    test_session.close()
    test_engine.dispose()
    print("PASS")