import logging
import hashlib
import base64
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

SERVICE_NAME = "score_badge_api"
PORT = 8791
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
PID_FILE = "/tmp/score_badge_api.pid"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(f"/home/workspace/logs/{SERVICE_NAME}.log")]
)
log = logging.getLogger(__name__)

app = FastAPI()


def ws_query(sql: str) -> dict:
    """Query DuckDB via write_service HTTP endpoint."""
    resp = requests.post(
        QUERY_URL,
        json={"sql": sql},
        timeout=15
    )
    resp.raise_for_status()
    return resp.json()


def ws_write(table: str, rows: list) -> dict:
    """Write to DuckDB via write_service HTTP endpoint."""
    resp = requests.post(
        WRITE_SERVICE_URL,
        json={"table": table, "rows": rows, "wait": True},
        timeout=15
    )
    resp.raise_for_status()
    return resp.json()


def get_server_verdict(server_id: str) -> Optional[dict]:
    """Fetch server verdict from mcp_server_registry."""
    sql = f"""
        SELECT server_id, name, trust_score, verdict, registry_source
        FROM mcp_server_registry
        WHERE server_id = '{server_id}'
        LIMIT 1
    """
    result = ws_query(sql)
    rows = result.get("rows", [])
    return rows[0] if rows else None


def get_signal_scores(server_id: str) -> list:
    """Fetch signal scores for a server."""
    sql = f"""
        SELECT signal_name, score, evidence, scored_at
        FROM mcp_signal_scores
        WHERE server_id = '{server_id}'
        ORDER BY signal_name
    """
    result = ws_query(sql)
    return result.get("rows", [])


def score_to_color(score: float) -> str:
    """Map trust score to color."""
    if score >= 80:
        return "#22c55e"
    elif score >= 60:
        return "#84cc16"
    elif score >= 40:
        return "#eab308"
    elif score >= 20:
        return "#f97316"
    else:
        return "#ef4444"


def verdict_to_color(verdict: str) -> str:
    """Map verdict to color."""
    colors = {
        "TRUSTED": "#22c55e",
        "AMBER": "#eab308",
        "UNTRUSTED": "#ef4444",
        "UNKNOWN": "#6b7280",
        "KNOWN_THREAT": "#991b1b",
        "HIGH_RISK_ISOLATED": "#f97316",
        "CAUTION_LIMITED": "#f59e0b",
        "AMBER_UNVERIFIED": "#f59e0b",
        "TRUSTED_RESEARCH": "#10b981",
        "ENTERPRISE_CONTROLLED": "#06b6d4",
    }
    return colors.get(verdict.upper(), "#6b7280")


def generate_svg_badge(score: float, label: str, width: int = 120, height: int = 24) -> str:
    """Generate SVG trust score badge."""
    pct = max(0, min(100, score))
    color = score_to_color(score)
    bar_width = int((width - 4) * (pct / 100))
    
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" rx="4" fill="#1e293b"/>
  <rect x="2" y="2" width="{bar_width}" height="{height - 4}" rx="2" fill="{color}"/>
  <text x="{width // 2}" y="{height // 2 + 1}" font-family="monospace" font-size="11" font-weight="bold" fill="white" text-anchor="middle" dominant-baseline="middle">{label}</text>
</svg>'''
    return svg


def generate_verdict_badge(verdict: str, width: int = 140, height: int = 24) -> str:
    """Generate SVG verdict badge."""
    color = verdict_to_color(verdict)
    display_verdict = verdict.replace("_", " ")
    
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" rx="4" fill="{color}"/>
  <text x="{width // 2}" y="{height // 2 + 1}" font-family="sans-serif" font-size="10" font-weight="bold" fill="white" text-anchor="middle" dominant-baseline="middle">{display_verdict}</text>
</svg>'''
    return svg


def generate_signal_badge_row(signal_name: str, score: float) -> str:
    """Generate SVG row for signal score."""
    pct = max(0, min(100, score))
    color = score_to_color(score)
    
    return f'''<g>
  <text x="0" y="14" font-family="sans-serif" font-size="10" fill="#94a3b8">{signal_name}</text>
  <rect x="90" y="5" width="80" height="10" rx="2" fill="#334155"/>
  <rect x="90" y="5" width="{int(80 * pct / 100)}" height="10" rx="2" fill="{color}"/>
  <text x="175" y="14" font-family="monospace" font-size="10" fill="#e2e8f0" text-anchor="end">{score:.1f}</text>
</g>'''


def generate_signal_panel(server_id: str, signals: list, width: int = 200, row_height: int = 20) -> str:
    """Generate SVG panel with all signal scores."""
    rows = []
    y_offset = 10
    for sig in signals:
        rows.append(f'<g transform="translate(0, {y_offset})">{generate_signal_badge_row(sig["signal_name"], sig["score"])}</g>')
        y_offset += row_height
    
    total_height = y_offset + 10
    signals_svg = '\n'.join(rows)
    
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{total_height}" viewBox="0 0 {width} {total_height}">
  <rect width="{width}" height="{total_height}" rx="6" fill="#0f172a"/>
  {signals_svg}
</svg>'''
    return svg


@app.get("/api/servers/{server_id}/badge")
async def get_trust_badge(server_id: str):
    """Get trust score SVG badge for a server."""
    server = get_server_verdict(server_id)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    score = server.get("trust_score", 0) or 0
    verdict = server.get("verdict", "UNKNOWN")
    
    svg = generate_svg_badge(score, f"{score:.0f}")
    
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=300"}
    )


@app.get("/api/servers/{server_id}/verdict-badge")
async def get_verdict_badge(server_id: str):
    """Get verdict SVG badge for a server."""
    server = get_server_verdict(server_id)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    verdict = server.get("verdict", "UNKNOWN")
    svg = generate_verdict_badge(verdict)
    
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=300"}
    )


@app.get("/api/servers/{server_id}/signals/badge")
async def get_signals_badge(server_id: str):
    """Get signal scores SVG badge panel for a server."""
    server = get_server_verdict(server_id)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    signals = get_signal_scores(server_id)
    if not signals:
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="40" viewBox="0 0 200 40">
  <rect width="200" height="40" rx="6" fill="#0f172a"/>
  <text x="100" y="25" font-family="sans-serif" font-size="11" fill="#94a3b8" text-anchor="middle">No signals scored</text>
</svg>'''
    else:
        svg = generate_signal_panel(server_id, signals)
    
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=300"}
    )


@app.get("/api/servers/{server_id}/badge/json")
async def get_trust_badge_json(server_id: str):
    """Get trust score badge data as JSON."""
    server = get_server_verdict(server_id)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    score = server.get("trust_score", 0) or 0
    verdict = server.get("verdict", "UNKNOWN")
    
    badge_data = {
        "server_id": server_id,
        "server_name": server.get("name"),
        "trust_score": score,
        "verdict": verdict,
        "color": score_to_color(score),
        "badge_url": f"/api/servers/{server_id}/badge",
        "verdict_badge_url": f"/api/servers/{server_id}/verdict-badge",
        "signals_badge_url": f"/api/servers/{server_id}/signals/badge",
        "generated_at": datetime.now(timezone.utc).isoformat()
    }
    return badge_data


@app.get("/api/servers/{server_id}/signals/json")
async def get_signals_json(server_id: str):
    """Get signal scores as JSON."""
    server = get_server_verdict(server_id)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    signals = get_signal_scores(server_id)
    
    return {
        "server_id": server_id,
        "signals": [
            {
                "name": s["signal_name"],
                "score": s["score"],
                "color": score_to_color(s["score"]),
                "scored_at": s.get("scored_at")
            }
            for s in signals
        ],
        "count": len(signals),
        "generated_at": datetime.now(timezone.utc).isoformat()
    }


@app.get("/api/batch/badge")
async def get_batch_badges(
    server_ids: str = Query(..., description="Comma-separated server IDs")
):
    """Get trust badges for multiple servers."""
    ids = [s.strip() for s in server_ids.split(",") if s.strip()]
    
    if len(ids) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 servers per request")
    
    results = []
    for sid in ids:
        server = get_server_verdict(sid)
        if server:
            score = server.get("trust_score", 0) or 0
            results.append({
                "server_id": sid,
                "server_name": server.get("name"),
                "trust_score": score,
                "verdict": server.get("verdict", "UNKNOWN"),
                "color": score_to_color(score),
                "badge_url": f"/api/servers/{sid}/badge"
            })
        else:
            results.append({
                "server_id": sid,
                "error": "Not found"
            })
    
    return {
        "results": results,
        "count": len(results),
        "generated_at": datetime.now(timezone.utc).isoformat()
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


if __name__ == "__main__":
    import uvicorn
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")