from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute
from .logic import get_dispute_status_aging

router = APIRouter()


@router.get("/disputes/status-aging")
def dispute_status_aging(session: Session = Depends(get_session)):
    return get_dispute_status_aging(session)


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

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
    McpScoreDispute.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    rows = [
        McpScoreDispute(
            server_id="server-1",
            submitted_by="user-1",
            proposed_overall_risk=0.8,
            proposed_axes={},
            reason_category="other",
            explanation="Self-test dispute",
            status="open",
            created_at=now - timedelta(hours=12),
            resolved_at=None,
            admin_note=None,
        ),
        McpScoreDispute(
            server_id="server-2",
            submitted_by="user-2",
            proposed_overall_risk=0.7,
            proposed_axes={},
            reason_category="other",
            explanation="Self-test dispute",
            status="under_review",
            created_at=now - timedelta(days=3),
            resolved_at=None,
            admin_note=None,
        ),
        McpScoreDispute(
            server_id="server-3",
            submitted_by="user-3",
            proposed_overall_risk=0.6,
            proposed_axes={},
            reason_category="other",
            explanation="Self-test dispute",
            status="approved",
            created_at=now - timedelta(days=15),
            resolved_at=now - timedelta(days=5),
            admin_note="Approved during self-test",
        ),
        McpScoreDispute(
            server_id="server-4",
            submitted_by="user-4",
            proposed_overall_risk=0.4,
            proposed_axes={},
            reason_category="other",
            explanation="Self-test dispute",
            status="rejected",
            created_at=now - timedelta(days=50),
            resolved_at=now - timedelta(days=10),
            admin_note="Rejected during self-test",
        ),
        McpScoreDispute(
            server_id="server-5",
            submitted_by="user-5",
            proposed_overall_risk=0.9,
            proposed_axes={},
            reason_category="other",
            explanation="Self-test dispute",
            status="approved",
            created_at=now - timedelta(days=1),
            resolved_at=now - timedelta(hours=1),
            admin_note="Approved during self-test",
        ),
        McpScoreDispute(
            server_id="server-6",
            submitted_by="user-6",
            proposed_overall_risk=0.2,
            proposed_axes={},
            reason_category="other",
            explanation="Self-test dispute",
            status="rejected",
            created_at=now - timedelta(days=7),
            resolved_at=now - timedelta(days=2),
            admin_note="Rejected during self-test",
        ),
    ]

    with TestSession() as session:
        session.add_all(rows)
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/disputes/status-aging")
    response.raise_for_status()
    payload = response.json()

    assert payload["total"] == 6
    assert len(payload["by_status"]) == 4
    assert sum(item["count"] for item in payload["by_status"]) == 6
    assert any(
        item["status"] == "approved" and item["count"] == 2
        for item in payload["by_status"]
    )
    assert len(payload["by_age_bucket"]) == 4
    assert sum(item["count"] for item in payload["by_age_bucket"]) == 6
    assert payload["generated_at"]

    print("PASS")