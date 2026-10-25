from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


router = APIRouter(prefix="/api")


class DisputeStatusAgeingResponse(BaseModel):
    total: int
    open_count: int
    missing_admin_note: int
    statuses: List["DisputeStatusAgeingEntry"] = Field(default_factory=list)


class DisputeStatusAgeingEntry(BaseModel):
    status: str
    count: int
    age_buckets: Dict[str, int]
    oldest_created_at: Optional[str] = None


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _timestamp_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _age_days(created_at: Any, resolved_at: Any, closed: bool) -> float:
    created = _as_utc(created_at)
    if created is None:
        return 0.0
    if closed:
        end = _as_utc(resolved_at) or datetime.now(timezone.utc)
    else:
        end = datetime.now(timezone.utc)
    seconds = (end - created).total_seconds()
    if seconds <= 0:
        return 0.0
    return seconds / 86400.0


def _age_bucket(age_days: float) -> str:
    if age_days <= 1:
        return "b0_1d"
    if age_days <= 7:
        return "b1_7d"
    if age_days <= 30:
        return "b7_30d"
    return "b30_plus"


def get_dispute_status_ageing(session: Session) -> DisputeStatusAgeingResponse:
    rows = session.execute(
        select(
            McpScoreDispute.id,
            McpScoreDispute.status,
            McpScoreDispute.admin_note,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
        )
    ).all()

    grouped: Dict[str, List[Any]] = {}
    total = 0
    open_count = 0
    missing_admin_note = 0
    now = datetime.now(timezone.utc)

    for row in rows:
        total += 1
        raw_status = row.status
        status = str(raw_status) if raw_status is not None else "unknown"
        status_key = status
        grouped.setdefault(status_key, [])

        note = row.admin_note
        if note is None or not str(note).strip():
            missing_admin_note += 1

        normalized_status = status.strip().lower()
        is_closed = normalized_status in {"resolved", "closed"}
        if not is_closed:
            open_count += 1

        created = _as_utc(row.created_at)
        end = _as_utc(row.resolved_at) if is_closed else now
        if created is None:
            age_days = 0.0
        elif end is None:
            age_days = (now - created).total_seconds() / 86400.0
        else:
            age_days = (end - created).total_seconds() / 86400.0
        if age_days < 0:
            age_days = 0.0

        grouped[status_key].append((row, _age_bucket(age_days), is_closed))

    statuses: List[DisputeStatusAgeingEntry] = []
    for status in sorted(grouped):
        entries = grouped[status]
        buckets = {
            "b0_1d": 0,
            "b1_7d": 0,
            "b7_30d": 0,
            "b30_plus": 0,
        }
        oldest_created_at: Optional[str] = None
        oldest_value: Optional[datetime] = None

        for row, bucket, is_closed in entries:
            buckets[bucket] += 1
            if not is_closed:
                created = _as_utc(row.created_at)
                if created is not None and (oldest_value is None or created < oldest_value):
                    oldest_value = created
                    oldest_created_at = _timestamp_string(row.created_at)

        statuses.append(
            DisputeStatusAgeingEntry(
                status=status,
                count=len(entries),
                age_buckets=buckets,
                oldest_created_at=oldest_created_at,
            )
        )

    return DisputeStatusAgeingResponse(
        total=total,
        open_count=open_count,
        missing_admin_note=missing_admin_note,
        statuses=statuses,
    )


compute_status_ageing = get_dispute_status_ageing
get_status_ageing = get_dispute_status_ageing


@router.get(
    "/disputes/status-ageing",
    response_model=DisputeStatusAgeingResponse,
)
def dispute_status_ageing(
    session: Session = Depends(get_session),
) -> DisputeStatusAgeingResponse:
    return get_dispute_status_ageing(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    connection = engine.connect()
    connection.execute(
        text(
            """
            CREATE TABLE mcp_score_disputes (
                id INTEGER PRIMARY KEY,
                server_id TEXT,
                submitted_by TEXT,
                proposed_overall_risk TEXT,
                proposed_axes TEXT,
                reason_category TEXT,
                explanation TEXT,
                status TEXT,
                admin_note TEXT,
                created_at TEXT,
                resolved_at TEXT
            )
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO mcp_score_disputes
                (id, server_id, submitted_by, proposed_overall_risk, proposed_axes,
                 reason_category, explanation, status, admin_note, created_at, resolved_at)
            VALUES
                (1, 'server-a', 'user-a', 'high', '{}', 'reason', 'explanation',
                 'open', NULL, :created_1, NULL),
                (2, 'server-b', 'user-b', 'medium', '{}', 'reason', 'explanation',
                 'open', 'reviewed', :created_2, NULL),
                (3, 'server-c', 'user-c', 'low', '{}', 'reason', 'explanation',
                 'closed', 'reviewed', :created_3, :resolved_3)
            """
        ),
        {
            "created_1": "2024-01-01T00:00:00+00:00",
            "created_2": "2024-01-03T00:00:00+00:00",
            "created_3": "2024-01-05T00:00:00+00:00",
            "resolved_3": "2024-01-06T00:00:00+00:00",
        },
    )
    connection.commit()

    testing_session = sessionmaker(bind=connection)()

    def override_get_session():
        yield testing_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(that_app)
    response = client.get("/api/disputes/status-ageing")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["open_count"] == 2
    assert payload["missing_admin_note"] == 1
    for status in payload["statuses"]:
        assert sum(status["age_buckets"].values()) == status["count"]

    print("PASS")