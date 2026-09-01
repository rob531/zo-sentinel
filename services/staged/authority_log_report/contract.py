from typing import Any
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["authority-log-report"])


class AuthorityActionStats(BaseModel):
    count: int
    last_timestamp: str


class AuthorityLogReportResponse(BaseModel):
    actions: dict[str, AuthorityActionStats]
    timestamp: str


def get_authority_log_report(session: Session = Depends(get_session)) -> dict[str, Any]:
    query = text("""
        SELECT 
            action,
            COUNT(*) as count,
            MAX(timestamp) as last_timestamp
        FROM audit_log
        WHERE action LIKE 'authority_%'
           OR action LIKE 'admin_%'
           OR action = 'user_promote'
           OR action = 'user_demote'
           OR action = 'role_assign'
           OR action = 'role_revoke'
           OR action = 'permission_grant'
           OR action = 'permission_revoke'
        GROUP BY action
        ORDER BY action
    """)
    result = session.execute(query)
    actions = {}
    for row in result:
        action, count, last_ts = row
        actions[action] = {
            "count": count,
            "last_timestamp": str(last_ts) if last_ts else None
        }
    return actions


@router.get("/authority-log-report", response_model=AuthorityLogReportResponse)
def authority_log_report_endpoint(session: Session = Depends(get_session)) -> AuthorityLogReportResponse:
    from datetime import datetime, timezone
    actions = get_authority_log_report(session)
    return AuthorityLogReportResponse(
        actions=actions,
        timestamp=datetime.now(timezone.utc).isoformat()
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker
    import sys

    local_app = FastAPI()
    local_app.include_router(router)

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    with test_engine.connect() as conn:
        conn.execute(text("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, action TEXT, timestamp TEXT, user_id INTEGER, details TEXT)"))
        conn.execute(text("INSERT INTO audit_log (action, timestamp, user_id) VALUES ('authority_user_create', '2024-01-15T10:30:00Z', 1)"))
        conn.execute(text("INSERT INTO audit_log (action, timestamp, user_id) VALUES ('authority_user_create', '2024-01-16T11:00:00Z', 1)"))
        conn.execute(text("INSERT INTO audit_log (action, timestamp, user_id) VALUES ('authority_user_delete', '2024-01-17T14:00:00Z', 2)"))
        conn.execute(text("INSERT INTO audit_log (action, timestamp, user_id) VALUES ('admin_role_assign', '2024-01-18T09:00:00Z', 1)"))
        conn.execute(text("INSERT INTO audit_log (action, timestamp, user_id) VALUES ('user_promote', '2024-01-19T10:00:00Z', 3)"))
        conn.execute(text("INSERT INTO audit_log (action, timestamp, user_id) VALUES ('role_revoke', '2024-01-20T11:00:00Z', 2)"))
        conn.execute(text("INSERT INTO audit_log (action, timestamp, user_id) VALUES ('read_data', '2024-01-21T12:00:00Z', 1)"))
        conn.commit()

    TestSessionLocal = sessionmaker(bind=test_engine)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    local_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    client = TestClient(local_app)

    response = client.get("/api/authority-log-report")

    if response.status_code != 200:
        print(f"FAIL: status {response.status_code}")
        sys.exit(1)

    data = response.json()
    if "actions" not in data or "timestamp" not in data:
        print("FAIL: missing required fields")
        sys.exit(1)

    actions = data["actions"]
    expected_actions = ["authority_user_create", "authority_user_delete", "admin_role_assign", "user_promote", "role_revoke"]
    for action in expected_actions:
        if action not in actions:
            print(f"FAIL: missing action {action}")
            sys.exit(1)
        if "count" not in actions[action] or "last_timestamp" not in actions[action]:
            print(f"FAIL: missing stats for action {action}")
            sys.exit(1)

    if actions["authority_user_create"]["count"] != 2:
        print(f"FAIL: wrong count for authority_user_create: {actions['authority_user_create']['count']}")
        sys.exit(1)
    if actions["authority_user_delete"]["count"] != 1:
        print(f"FAIL: wrong count for authority_user_delete: {actions['authority_user_delete']['count']}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)