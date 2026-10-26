import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException

SERVICE_NAME = "scoring_freshness_dashboard"
LOG_FILE = f"/home/workspace/logs/{SERVICE_NAME}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_ENDPOINT = f"{WRITE_SERVICE_URL}/query"
WRITE_ENDPOINT = f"{WRITE_SERVICE_URL}/write"

router = APIRouter()


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Execute a read-only SQL query via write_service."""
    try:
        resp = requests.post(QUERY_ENDPOINT, json={"sql": sql}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except requests.RequestException as exc:
        logger.error("ws_query failed for SQL %s: %s", sql[:100], exc)
        raise HTTPException(status_code=502, detail=f"Query service unavailable: {exc}") from exc


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def compute_freshness_hours(last_scored_at: Optional[str]) -> Optional[float]:
    """Compute hours since a timestamp string, or None if invalid/missing."""
    if not last_scored_at:
        return None
    try:
        scored = datetime.fromisoformat(last_scored_at.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - scored
        return round(delta.total_seconds() / 3600, 2)
    except (ValueError, TypeError):
        return None


@router.get("/health")
def health() -> Dict[str, Any]:
    """Liveness probe for the dashboard service."""
    return {"status": "ok", "service": SERVICE_NAME, "ts": utc_now_iso()}


@router.get("/freshness/summary")
def get_freshness_summary() -> Dict[str, Any]:
    """
    Return a summary of scoring freshness across all servers.

    Uses mcp_signal_scores.scored_at (TIMESTAMPTZ) as the primary
    freshness signal. Servers without any score are counted separately.
    """
    total_sql = "SELECT COUNT(DISTINCT server_id) AS total FROM mcp_server_registry"
    scored_sql = """
        SELECT COUNT(DISTINCT server_id) AS scored
        FROM mcp_signal_scores
        WHERE scored_at IS NOT NULL
    """
    stale_sql = """
        SELECT COUNT(DISTINCT server_id) AS stale
        FROM mcp_signal_scores
        WHERE scored_at IS NOT NULL
          AND scored_at < NOW() - INTERVAL '7 days'
    """
    try:
        total_rows = ws_query(total_sql)
        scored_rows = ws_query(scored_sql)
        stale_rows = ws_query(stale_sql)
        total = total_rows[0]["total"] if total_rows else 0
        scored = scored_rows[0]["scored"] if scored_rows else 0
        stale = stale_rows[0]["stale"] if stale_rows else 0
        unscored = total - scored
        return {
            "total_servers": total,
            "scored_servers": scored,
            "unscored_servers": unscored,
            "stale_servers": stale,
            "ts": utc_now_iso(),
        }
    except Exception as exc:
        logger.error("freshness_summary failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/freshness/servers")
def get_servers_by_freshness(
    limit: int = 50,
    stale_only: bool = False,
) -> Dict[str, Any]:
    """
    Return servers ordered by scoring freshness.

    JOINs mcp_server_registry (name, server_id) with the latest
    scored_at from mcp_signal_scores.  Stale servers are those
    with no score in the last 7 days.
    """
    if stale_only:
        where_clause = "AND ss.scored_at < NOW() - INTERVAL '7 days'"
    else:
        where_clause = ""

    sql = f"""
        SELECT
            r.server_id,
            r.name,
            r.verdict,
            r.trust_score,
            ss.signal_name,
            ss.score,
            ss.scored_at,
            ss.evidence
        FROM mcp_server_registry r
        LEFT JOIN LATERAL (
            SELECT signal_name, score, scored_at, evidence
            FROM mcp_signal_scores s
            WHERE s.server_id = r.server_id
            ORDER BY scored_at DESC
            LIMIT 1
        ) ss ON true
        WHERE ss.scored_at IS NOT NULL {where_clause}
        ORDER BY ss.scored_at ASC NULLS LAST
        LIMIT ?
    """
    try:
        rows = ws_query(sql.replace("?", f"{limit}"))
        result = []
        for row in rows:
            scored_at = row.get("scored_at")
            freshness_hours = compute_freshness_hours(scored_at)
            result.append({
                "server_id": row.get("server_id"),
                "name": row.get("name"),
                "verdict": row.get("verdict"),
                "trust_score": row.get("trust_score"),
                "latest_signal": row.get("signal_name"),
                "latest_score": row.get("score"),
                "scored_at": scored_at,
                "freshness_hours": freshness_hours,
            })
        return {"servers": result, "count": len(result), "ts": utc_now_iso()}
    except Exception as exc:
        logger.error("servers_by_freshness failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/freshness/signal-types")
def get_signal_type_coverage() -> Dict[str, Any]:
    """
    Return per-signal-type coverage and freshness statistics.

    Uses mcp_signal_scores.signal_name and mcp_signal_scores.scored_at.
    """
    sql = """
        SELECT
            signal_name,
            COUNT(*) AS total_rows,
            COUNT(DISTINCT server_id) AS servers_scored,
            MIN(scored_at) AS oldest_computed,
            MAX(scored_at) AS newest_computed
        FROM mcp_signal_scores
        WHERE scored_at IS NOT NULL
        GROUP BY signal_name
        ORDER BY signal_name
    """
    try:
        rows = ws_query(sql)
        result = []
        for row in rows:
            oldest = row.get("oldest_computed")
            newest = row.get("newest_computed")
            result.append({
                "signal_name": row.get("signal_name"),
                "total_rows": row.get("total_rows"),
                "servers_scored": row.get("servers_scored"),
                "oldest_computed": oldest,
                "newest_computed": newest,
                "oldest_hours": compute_freshness_hours(oldest),
                "newest_hours": compute_freshness_hours(newest),
            })
        return {"signal_types": result, "count": len(result), "ts": utc_now_iso()}
    except Exception as exc:
        logger.error("signal_type_coverage failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/freshness/registry")
def get_registry_freshness() -> Dict[str, Any]:
    """
    Return scoring status for every server in mcp_server_registry,
    including verdict distribution for scored vs unscored servers.
    """
    sql = """
        SELECT
            r.server_id,
            r.name,
            r.verdict,
            r.trust_score,
            r.registry_source,
            r.last_seen,
            r.last_scanned,
            (
                SELECT MAX(ss.scored_at)
                FROM mcp_signal_scores ss
                WHERE ss.server_id = r.server_id
            ) AS latest_score_at,
            (
                SELECT COUNT(*)
                FROM mcp_signal_scores ss
                WHERE ss.server_id = r.server_id
            ) AS score_count
        FROM mcp_server_registry r
        ORDER BY latest_score_at ASC NULLS LAST
        LIMIT 200
    """
    try:
        rows = ws_query(sql)
        scored = 0
        unscored = 0
        verdict_counts: Dict[str, int] = {}
        result = []
        for row in rows:
            latest = row.get("latest_score_at")
            freshness_hours = compute_freshness_hours(latest)
            verdict = row.get("verdict") or "unknown"
            if freshness_hours is not None:
                scored += 1
            else:
                unscored += 1
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
            result.append({
                "server_id": row.get("server_id"),
                "name": row.get("name"),
                "verdict": verdict,
                "trust_score": row.get("trust_score"),
                "registry_source": row.get("registry_source"),
                "latest_score_at": latest,
                "freshness_hours": freshness_hours,
                "score_count": row.get("score_count"),
            })
        return {
            "servers": result,
            "summary": {
                "scored": scored,
                "unscored": unscored,
                "verdict_counts": verdict_counts,
            },
            "ts": utc_now_iso(),
        }
    except Exception as exc:
        logger.error("registry_freshness failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def run() -> None:
    """Entrypoint for uvicorn daemon mode."""
    import uvicorn
    from fastapi import FastAPI

    app = FastAPI(title=SERVICE_NAME)
    app.include_router(router)

    @app.get("/health")
    def daemon_health() -> Dict[str, Any]:
        return {"status": "ok", "service": SERVICE_NAME, "ts": utc_now_iso()}

    logger.info("Starting %s on port 0", SERVICE_NAME)
    uvicorn.run(app, host="0.0.0.0", port=0, log_level="info")


if __name__ == "__main__":
    run()
    import sys
    sys.exit(0)