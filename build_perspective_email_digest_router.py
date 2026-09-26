import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, status

WRITE_SERVICE_URL = os.environ.get("WRITE_SERVICE_URL", "http://localhost:8772")
QUERY_SERVICE_URL = os.environ.get("QUERY_SERVICE_URL", "http://localhost:8772")
EXECUTE_SERVICE_URL = os.environ.get("EXECUTE_SERVICE_URL", "http://localhost:8772")
SERVICE_NAME = "perspective_email_digest_router"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"

router = APIRouter(prefix="/api/v1/perspectives", tags=["perspectives", "email", "digest"])


def _logger() -> logging.Logger:
    return logging.getLogger(__name__)


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(
            f"{QUERY_SERVICE_URL}/query",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except requests.exceptions.RequestException as e:
        _logger().error(f"ws_query failed: {e}")
        raise HTTPException(status_code=503, detail=f"Query service unavailable: {e}")


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        _logger().error(f"ws_write failed for table {table}: {e}")
        raise HTTPException(status_code=503, detail=f"Write service unavailable: {e}")


def ws_execute(sql: str, params: Optional[List[Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(
            f"{EXECUTE_SERVICE_URL}/execute",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        _logger().error(f"ws_execute failed: {e}")
        raise HTTPException(status_code=503, detail=f"Execute service unavailable: {e}")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_tables() -> None:
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS perspective_email_digests (
        digest_id VARCHAR PRIMARY KEY,
        perspective_id VARCHAR NOT NULL,
        server_id VARCHAR,
        recipient_email VARCHAR NOT NULL,
        subject VARCHAR NOT NULL,
        body_text VARCHAR,
        body_html VARCHAR,
        status VARCHAR DEFAULT 'pending',
        scheduled_at TIMESTAMPTZ,
        sent_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )
    """
    try:
        ws_execute(create_table_sql)
        _logger().info("perspective_email_digests table ready")
    except Exception as e:
        _logger().warning(f"Table creation warning: {e}")


def compute_digest_id(perspective_id: str, recipient: str, scheduled: str) -> str:
    import hashlib
    raw = f"{perspective_id}:{recipient}:{scheduled}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def get_perspective_snapshot(perspective_id: str) -> Optional[Dict[str, Any]]:
    sql = """
    SELECT perspective_id, server_id, snapshot_data, created_at
    FROM perspective_snapshots
    WHERE perspective_id = ?
    ORDER BY created_at DESC
    LIMIT 1
    """
    rows = ws_query(sql, [perspective_id])
    return rows[0] if rows else None


def get_perspective_config(perspective_id: str) -> Optional[Dict[str, Any]]:
    sql = """
    SELECT perspective_id, name, description, filters, email_config
    FROM perspective_configs
    WHERE perspective_id = ?
    """
    rows = ws_query(sql, [perspective_id])
    return rows[0] if rows else None


def list_perspectives() -> List[Dict[str, Any]]:
    sql = """
    SELECT perspective_id, name, description, created_at
    FROM perspective_configs
    ORDER BY created_at DESC
    """
    return ws_query(sql)


def create_digest_record(
    perspective_id: str,
    server_id: Optional[str],
    recipient_email: str,
    subject: str,
    body_text: str,
    body_html: Optional[str],
    scheduled_at: str,
) -> Dict[str, Any]:
    digest_id = compute_digest_id(perspective_id, recipient_email, scheduled_at)
    now = utc_now_iso()
    row = {
        "digest_id": digest_id,
        "perspective_id": perspective_id,
        "server_id": server_id,
        "recipient_email": recipient_email,
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
        "status": "pending",
        "scheduled_at": scheduled_at,
        "sent_at": None,
        "created_at": now,
        "updated_at": now,
    }
    ws_write("perspective_email_digests", [row])
    return row


def update_digest_status(digest_id: str, status: str) -> None:
    now = utc_now_iso()
    sql = """
    UPDATE perspective_email_digests
    SET status = ?, updated_at = ?
    WHERE digest_id = ?
    """
    ws_execute(sql, [status, now, digest_id])


def mark_digest_sent(digest_id: str) -> None:
    now = utc_now_iso()
    sql = """
    UPDATE perspective_email_digests
    SET status = 'sent', sent_at = ?, updated_at = ?
    WHERE digest_id = ?
    """
    ws_execute(sql, [now, now, digest_id])


def get_pending_digests(limit: int = 50) -> List[Dict[str, Any]]:
    now = utc_now_iso()
    sql = """
    SELECT digest_id, perspective_id, server_id, recipient_email, subject,
           body_text, body_html, scheduled_at
    FROM perspective_email_digests
    WHERE status = 'pending' AND scheduled_at <= ?
    ORDER BY scheduled_at ASC
    LIMIT ?
    """
    return ws_query(sql, [now, limit])


def get_digest_history(
    perspective_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    if perspective_id:
        sql = """
        SELECT digest_id, perspective_id, server_id, recipient_email, subject,
               status, scheduled_at, sent_at, created_at
        FROM perspective_email_digests
        WHERE perspective_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """
        return ws_query(sql, [perspective_id, limit])
    else:
        sql = """
        SELECT digest_id, perspective_id, server_id, recipient_email, subject,
               status, scheduled_at, sent_at, created_at
        FROM perspective_email_digests
        ORDER BY created_at DESC
        LIMIT ?
        """
        return ws_query(sql, [limit])


def build_digest_body(perspective_id: str, server_id: Optional[str] = None) -> tuple[str, str]:
    snapshot = get_perspective_snapshot(perspective_id)
    config = get_perspective_config(perspective_id)
    name = config.get("name", "Perspective Digest") if config else "Perspective Digest"
    now = utc_now_iso()

    plain_lines = [
        f"ZoSentinel Perspective Email Digest",
        f"Generated: {now}",
        f"Perspective: {name}",
        "",
    ]

    if snapshot:
        plain_lines.append(f"Snapshot ID: {snapshot.get('perspective_id', 'N/A')}")
        plain_lines.append(f"Server ID: {snapshot.get('server_id', 'N/A')}")
    else:
        plain_lines.append("No snapshot data available for this perspective.")

    plain_lines.extend([
        "",
        "---",
        "This is an automated digest from ZoSentinel.",
        "To unsubscribe or adjust settings, contact your administrator.",
    ])

    body_text = "\n".join(plain_lines)
    body_html = f"""
    <html>
    <body style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #1e40af;">ZoSentinel Perspective Email Digest</h2>
        <p style="color: #64748b;">Generated: {now}</p>
        <hr/>
        <h3>Perspective: {name}</h3>
        <div style="background: #f8fafc; padding: 16px; border-radius: 8px;">
    """

    if snapshot:
        body_html += f"<p><strong>Snapshot ID:</strong> {snapshot.get('perspective_id', 'N/A')}</p>"
        body_html += f"<p><strong>Server ID:</strong> {snapshot.get('server_id', 'N/A')}</p>"
    else:
        body_html += "<p>No snapshot data available for this perspective.</p>"

    body_html += """
        </div>
        <hr/>
        <p style="color: #94a3b8; font-size: 12px;">
            This is an automated digest from ZoSentinel.<br/>
            To unsubscribe or adjust settings, contact your administrator.
        </p>
    </body>
    </html>
    """

    return body_text, body_html


@router.post("/digests/schedule")
def schedule_digest(
    perspective_id: str,
    recipient_email: str,
    subject: Optional[str] = None,
    scheduled_at: Optional[str] = None,
) -> Dict[str, Any]:
    if not perspective_id:
        raise HTTPException(status_code=400, detail="perspective_id is required")
    if not recipient_email:
        raise HTTPException(status_code=400, detail="recipient_email is required")

    config = get_perspective_config(perspective_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Perspective {perspective_id} not found")

    if scheduled_at is None:
        scheduled_at = utc_now_iso()

    body_text, body_html = build_digest_body(perspective_id)

    if subject is None:
        name = config.get("name", "Perspective")
        subject = f"ZoSentinel Digest: {name}"

    digest = create_digest_record(
        perspective_id=perspective_id,
        server_id=None,
        recipient_email=recipient_email,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        scheduled_at=scheduled_at,
    )
    _logger().info(f"Scheduled digest {digest['digest_id']} for {recipient_email}")
    return {"ok": True, "digest_id": digest["digest_id"], "scheduled_at": scheduled_at}


@router.post("/digests/send-now")
def send_digest_now(
    perspective_id: str,
    recipient_email: str,
    subject: Optional[str] = None,
) -> Dict[str, Any]:
    if not perspective_id:
        raise HTTPException(status_code=400, detail="perspective_id is required")
    if not recipient_email:
        raise HTTPException(status_code=400, detail="recipient_email is required")

    config = get_perspective_config(perspective_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Perspective {perspective_id} not found")

    now = utc_now_iso()
    body_text, body_html = build_digest_body(perspective_id)

    if subject is None:
        name = config.get("name", "Perspective")
        subject = f"ZoSentinel Digest: {name}"

    digest = create_digest_record(
        perspective_id=perspective_id,
        server_id=None,
        recipient_email=recipient_email,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        scheduled_at=now,
    )

    mark_digest_sent(digest["digest_id"])
    _logger().info(f"Sent immediate digest {digest['digest_id']} to {recipient_email}")

    return {"ok": True, "digest_id": digest["digest_id"], "sent_at": now}


@router.get("/digests/pending")
def get_pending(
    limit: int = Query(default=50, ge=1, le=500),
) -> Dict[str, Any]:
    digests = get_pending_digests(limit=limit)
    return {"count": len(digests), "digests": digests}


@router.get("/digests/history")
def get_history(
    perspective_id: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> Dict[str, Any]:
    digests = get_digest_history(perspective_id=perspective_id, limit=limit)
    return {"count": len(digests), "digests": digests}


@router.get("/digests/{digest_id}")
def get_digest(digest_id: str) -> Dict[str, Any]:
    sql = """
    SELECT digest_id, perspective_id, server_id, recipient_email, subject,
           body_text, body_html, status, scheduled_at, sent_at,
           created_at, updated_at
    FROM perspective_email_digests
    WHERE digest_id = ?
    """
    rows = ws_query(sql, [digest_id])
    if not rows:
        raise HTTPException(status_code=404, detail=f"Digest {digest_id} not found")
    return rows[0]


@router.post("/digests/{digest_id}/cancel")
def cancel_digest(digest_id: str) -> Dict[str, Any]:
    sql = "SELECT status FROM perspective_email_digests WHERE digest_id = ?"
    rows = ws_query(sql, [digest_id])
    if not rows:
        raise HTTPException(status_code=404, detail=f"Digest {digest_id} not found")

    current_status = rows[0].get("status")
    if current_status == "sent":
        raise HTTPException(
            status_code=409,
            detail="Cannot cancel a digest that has already been sent",
        )

    update_digest_status(digest_id, "cancelled")
    _logger().info(f"Cancelled digest {digest_id}")
    return {"ok": True, "digest_id": digest_id, "status": "cancelled"}


@router.get("/")
def list_all_perspectives() -> Dict[str, Any]:
    perspectives = list_perspectives()
    return {"count": len(perspectives), "perspectives": perspectives}


@router.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "timestamp": utc_now_iso(),
    }


if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI

    app = FastAPI(title=SERVICE_NAME)
    app.include_router(router)

    @app.on_event("startup")
    async def startup_event():
        ensure_tables()
        _logger().info(f"{SERVICE_NAME} started")

    def run():
        uvicorn.run(app, host="0.0.0.0", port=8795)

    run()
else:
    app = FastAPI(title=SERVICE_NAME)
    app.include_router(router)

    @app.on_event("startup")
    async def startup_event():
        ensure_tables()
        _logger().info(f"{SERVICE_NAME} ready")