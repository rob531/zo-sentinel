#!/usr/bin/env python3
"""ZO-SENTINEL: MCP Detail View UI - Port 8790"""
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
import uvicorn, requests

WRITE = "http://127.0.0.1:8772"
app = FastAPI()

def hb():
    try:
        requests.post(f"{WRITE}/write", json={"table": "service_health", "rows": {"service": "mcp_detail_view_ui", "last_heartbeat": datetime.utcnow().isoformat()}, "wait": True}, timeout=5)
    except: pass

@app.get("/health")
async def health(): return {"status": "ok", "service": "mcp_detail_view_ui"}

@app.get("/", response_class=HTMLResponse)
async def index():
    return """<!DOCTYPE html>
<html>
<head><title>MCP Detail View - ZO-SENTINEL</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
</head>
<body>
<div class="container mt-4">
<h1>MCP Detail Viewer</h1>
<form onsubmit="event.preventDefault(); fetchDetail(document.getElementById('serverId').value);">
<div class="input-group mb-3">
<input type="text" id="serverId" class="form-control" placeholder="Enter Server ID" required>
<button class="btn btn-primary" type="submit">Fetch Detail</button>
</div>
</form>
<div id="result" class="mt-4"></div>
</div>
<script>
async function fetchDetail(id) {
    const resp = await fetch('http://127.0.0.1:8779/server/' + id);
    const data = await resp.json();
    document.getElementById('result').innerHTML = '<pre>' + JSON.stringify(data, null, 2) + '</pre>';
}
</script>
</body></html>"""

@app.get("/server/{server_id}")
async def get_server(server_id: str):
    try:
        resp = requests.post(f"{WRITE}/query", json={"sql": f"SELECT * FROM mcp_server_registry WHERE server_id = '{server_id}'"}, timeout=10)
        rows = resp.json().get("rows", [])
        if not rows: raise HTTPException(status_code=404, detail="Server not found")
        return rows[0]
    except: raise HTTPException(status_code=500, detail="Service unavailable")

if __name__ == "__main__": uvicorn.run(app, host="127.0.0.1", port=8790)
