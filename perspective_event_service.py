import requests
import threading
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

from app.db import get_session
from app.models import (
    PerspectiveSnapshot,
    PerspectiveEvent,
    McpServerRegistry,
)
from sqlalchemy import text

WRITE_SERVICE = "http://127.0.0.1:8772"
HEALTH_SERVICE = "http://127.0.0.1:8772"


def _emit_events(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    resp = requests.post(
        f"{WRITE_SERVICE}/write",
        json={"table": "perspective_events", "rows": rows},
        timeout=10
    )
    resp.raise_for_status()


def _get_perspective_membership(session) -> List[Dict[str, Any]]:
    stmt = text("""
        SELECT DISTINCT perspective_id, server_id
        FROM perspective_snapshots
    """)
    result = session.execute(stmt)
    return [{"perspective_id": row[0], "server_id": row[1]} for row in result]


def _get_server_tier(server_id: str, session) -> Optional[str]:
    stmt = text("""
        SELECT risk_tier FROM mcp_server_registry
        WHERE server_id = :server_id
    """)
    result = session.execute(stmt, {"server_id": server_id}).fetchone()
    return result[0] if result else None


def _get_last_tier(perspective_id: str, server_id: str, session) -> Optional[str]:
    stmt = text("""
        SELECT new_tier FROM perspective_events
        WHERE perspective_id = :perspective_id
          AND server_id = :server_id
        ORDER BY id DESC
        LIMIT 1
    """)
    result = session.execute(stmt, {
        "perspective_id": perspective_id,
        "server_id": server_id
    }).fetchone()
    return result[0] if result else None


def _check_event_exists(perspective_id: str, server_id: str, change_type: str, session) -> bool:
    stmt = text("""
        SELECT 1 FROM perspective_events
        WHERE perspective_id = :perspective_id
          AND server_id = :server_id
          AND change_type = :change_type
        LIMIT 1
    """)
    result = session.execute(stmt, {
        "perspective_id": perspective_id,
        "server_id": server_id,
        "change_type": change_type
    }).fetchone()
    return result is not None


def detect_and_emit_events() -> int:
    session = next(get_session())
    membership = _get_perspective_membership(session)
    rows_to_emit = []

    for item in membership:
        perspective_id = item["perspective_id"]
        server_id = item["server_id"]

        current_tier = _get_server_tier(server_id, session)
        if current_tier is None:
            continue

        last_tier = _get_last_tier(perspective_id, server_id, session)
        if last_tier is None:
            continue

        if current_tier != last_tier:
            tier_order = {"D": 0, "C": 1, "B": 2, "A": 3}
            current_order = tier_order.get(current_tier, -1)
            last_order = tier_order.get(last_tier, -1)

            if current_order > last_order:
                change_type = "upgrade"
            else:
                change_type = "downgrade"

            if not _check_event_exists(perspective_id, server_id, change_type, session):
                rows_to_emit.append({
                    "perspective_id": perspective_id,
                    "server_id": server_id,
                    "change_type": change_type,
                    "old_tier": last_tier,
                    "new_tier": current_tier,
                    "seen": False
                })

    if rows_to_emit:
        _emit_events(rows_to_emit)

    session.close()
    return len(rows_to_emit)


def run() -> None:
    heartbeat_interval = 60

    def heartbeat():
        while True:
            try:
                requests.post(
                    f"{HEALTH_SERVICE}/health",
                    json={"service": "perspective_event_service", "status": "running"},
                    timeout=5
                )
            except Exception:
                pass
            time.sleep(heartbeat_interval)

    hb_thread = threading.Thread(target=heartbeat, daemon=True)
    hb_thread.start()

    while True:
        try:
            detect_and_emit_events()
        except requests.RequestException:
            pass
        time.sleep(30)


if __name__ == "__main__":
    from app.db import get_session as _orig_get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    def _test_session():
        yield test_session

    import app.dependency_overrides as dependency_overrides
    dependency_overrides[_orig_get_session] = _test_session

    server_id = "test_server_001"
    perspective_id = "test_perspective_001"

    test_session.execute(text("""
        INSERT INTO mcp_server_registry (server_id, risk_tier)
        VALUES (:server_id, :tier)
    """), {"server_id": server_id, "tier": "A"})
    test_session.execute(text("""
        INSERT INTO perspective_snapshots (perspective_id, server_id)
        VALUES (:persp_id, :srv_id)
    """), {"persp_id": perspective_id, "srv_id": server_id})
    test_session.commit()

    emitted = detect_and_emit_events()
    assert emitted == 1, f"Expected 1 event, got {emitted}"

    emitted2 = detect_and_emit_events()
    assert emitted2 == 0, f"Expected 0 events (idempotency), got {emitted2}"

    result = test_session.execute(text("""
        SELECT perspective_id, server_id, change_type, old_tier, new_tier
        FROM perspective_events
    """)).fetchall()
    assert len(result) == 1, f"Expected 1 row, got {len(result)}"
    assert result[0][0] == perspective_id
    assert result[0][1] == server_id
    assert result[0][2] == "upgrade"
    assert result[0][3] == "A"
    assert result[0][4] == "A"

    dependency_overrides.clear()
    test_session.close()
    test_engine.dispose()

    print("PASS")