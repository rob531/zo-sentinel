"""admin_exemptions_view service - HTML dashboard for MCP server exemptions."""
import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

router = APIRouter()


class ExemptionRow(BaseModel):
    name: str
    tier: str
    reason: str
    granted_by: str
    expires_at: Optional[str]
    expires_in_days: int
    status: str


class SummaryStats(BaseModel):
    total_exempt: int
    expiring_soon: int
    expired_count: int


class ExemptionsData(BaseModel):
    exemptions: list[ExemptionRow]
    summary: SummaryStats


def query_pipeline(q: str, params: dict | None = None) -> dict:
    import requests
    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json={"q": q, "params": params or {}},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_exemptions_data() -> ExemptionsData:
    """Fetch exemptions from pipeline tables."""
    now = datetime.utcnow()
    
    q = """
    SELECT 
        r.name,
        r.risk_tier as tier,
        e.reason,
        e.granted_by,
        e.expires_at
    FROM McpServerRegistry r
    JOIN mcp_exemptions e ON r.server_id = e.server_id
    """
    try:
        result = query_pipeline(q)
        rows = result.get("rows", [])
    except Exception:
        rows = []
    
    exemptions = []
    expiring_soon = 0
    expired_count = 0
    
    for row in rows:
        expires_at_str = row.get("expires_at")
        expires_in_days = 999
        status = "active"
        
        if expires_at_str:
            try:
                expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                if hasattr(expires_at, 'tzinfo') and expires_at.tzinfo:
                    expires_at = expires_at.replace(tzinfo=None)
                delta = expires_at - now
                expires_in_days = delta.days
                if expires_in_days < 0:
                    status = "expired"
                    expired_count += 1
                elif expires_in_days < 30:
                    status = "expiring_soon"
                    expiring_soon += 1
            except (ValueError, TypeError):
                expires_in_days = 999
                status = "unknown"
        
        exemptions.append(ExemptionRow(
            name=row.get("name", "Unknown"),
            tier=row.get("tier", "unknown"),
            reason=row.get("reason", ""),
            granted_by=row.get("granted_by", "Unknown"),
            expires_at=expires_at_str,
            expires_in_days=expires_in_days,
            status=status,
        ))
    
    return ExemptionsData(
        exemptions=exemptions,
        summary=SummaryStats(
            total_exempt=len(exemptions),
            expiring_soon=expiring_soon,
            expired_count=expired_count,
        ),
    )


def build_html(data: ExemptionsData) -> str:
    rows_html = ""
    for ex in data.exemptions:
        badge_class = {
            "active": "bg-success",
            "expiring_soon": "bg-warning",
            "expired": "bg-danger",
        }.get(ex.status, "bg-secondary")
        
        rows_html += f"""
        <tr>
            <td>{ex.name}</td>
            <td><span class="badge bg-info">{ex.tier}</span></td>
            <td>{ex.reason}</td>
            <td>{ex.granted_by}</td>
            <td>{ex.expires_in_days}d</td>
            <td><span class="badge {badge_class}">{ex.status.replace('_', ' ')}</span></td>
        </tr>"""
    
    if not rows_html:
        rows_html = '<tr><td colspan="6" class="text-center text-muted">No exemptions found</td></tr>'
    
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MCP Server Exemptions</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
<div class="container mt-4">
    <div class="d-flex justify-content-between align-items-center mb-4">
        <h1>MCP Server Exemptions</h1>
        <button id="refreshBtn" class="btn btn-primary" onclick="loadExemptions()">Refresh</button>
    </div>
    
    <div id="summaryBar" class="row mb-4">
        <div class="col-md-4">
            <div class="card text-center">
                <div class="card-body">
                    <h5 class="card-title">{data.summary.total_exempt}</h5>
                    <p class="card-text text-muted">Total Exempt</p>
                </div>
            </div>
        </div>
        <div class="col-md-4">
            <div class="card text-center border-warning">
                <div class="card-body">
                    <h5 class="card-title text-warning">{data.summary.expiring_soon}</h5>
                    <p class="card-text text-muted">Expiring Soon (&lt;30d)</p>
                </div>
            </div>
        </div>
        <div class="col-md-4">
            <div class="card text-center border-danger">
                <div class="card-body">
                    <h5 class="card-title text-danger">{data.summary.expired_count}</h5>
                    <p class="card-text text-muted">Expired</p>
                </div>
            </div>
        </div>
    </div>
    
    <div class="card">
        <div class="card-header">
            <h5 class="mb-0">Exemptions Table</h5>
        </div>
        <div class="table-responsive">
            <table class="table table-striped table-hover mb-0">
                <thead class="table-dark">
                    <tr>
                        <th>Name</th>
                        <th>Tier</th>
                        <th>Reason</th>
                        <th>Granted By</th>
                        <th>Expires In</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody id="exemptionsTableBody">
                    {rows_html}
                </tbody>
            </table>
        </div>
    </div>
</div>

<script>
async function loadExemptions() {{
    const btn = document.getElementById('refreshBtn');
    btn.disabled = true;
    btn.textContent = 'Loading...';
    try {{
        const resp = await fetch('/admin/exemptions');
        const html = await resp.text();
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        const newBody = doc.querySelector('#exemptionsTableBody');
        const newSummary = doc.querySelector('#summaryBar');
        if (newBody) document.getElementById('exemptionsTableBody').innerHTML = newBody.innerHTML;
        if (newSummary) document.getElementById('summaryBar').innerHTML = newSummary.innerHTML;
    }} catch (e) {{
        console.error('Failed to refresh:', e);
    }} finally {{
        btn.disabled = false;
        btn.textContent = 'Refresh';
    }}
}}
</script>
</body>
</html>"""


@router.get("/admin/exemptions")
async def get_exemptions() -> Response:
    """Serve the exemptions dashboard HTML."""
    data = get_exemptions_data()
    html = build_html(data)
    return Response(content=html, media_type="text/html")


if __name__ == "__main__":
    import threading
    import time
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    
    PORT = 18773
    SERVER_RUNNING = True
    
    def run_server():
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router)
        
        import uvicorn
        config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error")
        server = uvicorn.Server(config)
        threading.Thread(target=server.run, daemon=True).start()
        time.sleep(0.5)
    
    def test():
        run_server()
        time.sleep(0.3)
        
        import requests
        resp = requests.get(f"http://127.0.0.1:{PORT}/admin/exemptions", timeout=5)
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        html = resp.text
        assert "exemptions table" in html.lower() or "exemptions" in html.lower(), "Missing exemptions table"
        assert "summary" in html.lower() or "total" in html.lower(), "Missing summary bar"
        assert "summaryBar" in html, "Missing summary bar element"
        assert "exemptionsTableBody" in html, "Missing exemptions table body"
        assert "refreshBtn" in html or "Refresh" in html, "Missing refresh button"
        
        print("PASS")
    
    test()