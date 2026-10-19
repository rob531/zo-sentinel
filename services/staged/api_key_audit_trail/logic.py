import datetime
from typing import List, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import ApiKey


class AuditEvent(BaseModel):
    timestamp: datetime.datetime = Field(..., description="When the event occurred")
    action: str = Field(..., description="Action performed")
    target_server_id: Optional[int] = Field(
        None, description="Related server identifier, if any"
    )
    detail: Optional[str] = Field(None, description="Additional free‑form detail")


class ApiKeyAuditResponse(BaseModel):
    key_id: int = Field(..., description="Identifier of the API key")
    label: str = Field(..., description="Human readable label of the API key")
    created_at: datetime.datetime = Field(..., description="When the key was created")
    events: List[AuditEvent] = Field(..., description="Chronological audit events")


def get_audit_trail(
    key_id: int,
    days: int = 30,
    session: Session = Depends(get_session),
) -> ApiKeyAuditResponse:
    """
    Retrieve audit‑log events for a given API key.

    - ``days`` limits the look‑back window (default 30, max 365).
    - Events are ordered chronologically.
    """
    if days < 0 or days > 365:
        raise HTTPException(
            status_code=400,
            detail="`days` must be between 0 and 365",
        )

    api_key = session.query(ApiKey).filter(ApiKey.id == key_id).first()
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")

    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)

    sql = text(
        """
        SELECT
            timestamp,
            action,
            target_server_id,
            detail
        FROM audit_log
        WHERE key_hash = :key_hash
          AND timestamp >= :cutoff
        ORDER BY timestamp ASC
        """
    )
    rows = session.execute(
        sql,
        {"key_hash": api_key.key_hash, "cutoff": cutoff},
    ).fetchall()

    events = [
        AuditEvent(
            timestamp=row.timestamp,
            action=row.action,
            target_server_id=row.target_server_id,
            detail=row.detail,
        )
        for row in rows
    ]

    return ApiKeyAuditResponse(
        key_id=api_key.id,
        label=api_key.label,
        created_at=api_key.created_at,
        events=events,
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (mirrors the real schema for the purpose of the test)
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    # Create tables defined in the ORM (ApiKey) and the raw audit_log table.
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                action TEXT NOT NULL,
                target_server_id INTEGER,
                detail TEXT,
                key_hash TEXT NOT NULL
            )
            """
            )
        )

    # ------------------------------------------------------------------- #
    # Seed test data
    # ------------------------------------------------------------------- #
    session = SessionLocal()
    try:
        # Two API keys
        key1 = ApiKey(
            id=1,
            key_hash="hash1",
            label="first-key",
            created_at=datetime.datetime.utcnow() - datetime.timedelta(days=10),
            org_id=1,
        )
        key2 = ApiKey(
            id=2,
            key_hash="hash2",
            label="second-key",
            created_at=datetime.datetime.utcnow() - datetime.timedelta(days=20),
            org_id=1,
        )
        session.add_all([key1, key2])
        session.flush()  # obtain PKs if needed

        now = datetime.datetime.utcnow()
        audit_rows = [
            # Events for key1
            {
                "timestamp": now - datetime.timedelta(days=5),
                "action": "create",
                "target_server_id": None,
                "detail": "key created",
                "key_hash": "hash1",
            },
            {
                "timestamp": now - datetime.timedelta(days=3),
                "action": "use",
                "target_server_id": 101,
                "detail": "accessed endpoint",
                "key_hash": "hash1",
            },
            {
                "timestamp": now - datetime.timedelta(days=1),
                "action": "revoke",
                "target_server_id": None,
                "detail": "key revoked by admin",
                "key_hash": "hash1",
            },
            # Events for key2
            {
                "timestamp": now - datetime.timedelta(days=15),
                "action": "create",
                "target_server_id": None,
                "detail": "key created",
                "key_hash": "hash2",
            },
            {
                "timestamp": now - datetime.timedelta(days=12),
                "action": "use",
                "target_server_id": 202,
                "detail": "accessed endpoint",
                "key_hash": "hash2",
            },
            {
                "timestamp": now - datetime.timedelta(days=2),
                "action": "use",
                "target_server_id": 203,
                "detail": "accessed another endpoint",
                "key_hash": "hash2",
            },
        ]

        for row in audit_rows:
            session.execute(
                text(
                    """
                INSERT INTO audit_log
                (timestamp, action, target_server_id, detail, key_hash)
                VALUES (:timestamp, :action, :target_server_id, :detail, :key_hash)
                """
                ),
                row,
            )
        session.commit()

        # ------------------------------------------------------------------- #
        # Run assertions
        # ------------------------------------------------------------------- #
        resp1 = get_audit_trail(key_id=1, days=30, session=session)
        resp2 = get_audit_trail(key_id=2, days=30, session=session)

        assert resp1.key_id == 1
        assert len(resp1.events) >= 3
        assert resp2.key_id == 2
        assert len(resp2.events) >= 3

        print("PASS")
    finally:
        session.close()