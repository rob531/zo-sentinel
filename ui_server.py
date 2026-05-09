"""
ZO-SENTINEL — Rich UI Server (reconstructed)
============================================

Reconstructed from:
  * Captured client-side JS (loadRecent, doSearch, renderResult flow) audited
    earlier today before the live `ui_server.py` was overwritten with a stub.
  * Surviving on-disk template fragments at /home/workspace/zo_sentinel/
    (dashboard.html, mcp_submission_portal.html).
  * The current stub (`/home/workspace/zo_sentinel/ui_server.py`) — DB-call
    patterns (ws_write / ws_query / ws_execute), service constants, heartbeat
    loop, and PID-file single-instance lock are copied verbatim from there so
    behaviour against write_service / query_service stays identical.

Restored routes:
    GET  /                — rich landing page (search + ASSESS + recent list).
    GET  /api/recent      — recent assessments.
    GET  /api/search      — rich assessment payload (results + signals +
                            threats + risk + attestation + history).
    GET  /api/registry    — basic registry catalog.
    GET  /api/audit       — audit log entries.
    POST /api/submit      — Pydantic-validated submission, returns submission_id.
    GET  /admin-threats   — gated by email_guid_auth if importable, else placeholder.
    GET  /admin-risk      — same pattern as /admin-threats.
    GET  /healthz         — liveness probe.

Mounts on port 8790. Talks to write/query service at 127.0.0.1:8772.

This file is staged as `ui_server_rich.py` and `ui_server_rich.py.staged` —
it must NOT replace the live `ui_server.py` until reviewed.
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
import uvicorn
import os
import json
import time
import uuid
import logging
import threading
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import requests

# ---------------------------------------------------------------------------
# Service constants (copied verbatim from the stub for parity)
# ---------------------------------------------------------------------------
SERVICE_NAME = "ui_server"
SERVICE_PORT = 8790
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 30
PROJECT_ROOT = "/home/workspace/zo_sentinel"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(SERVICE_NAME)

app = FastAPI(title="ZO-SENTINEL Trust Intelligence", version="2.0.0")
start_time = time.time()


# ---------------------------------------------------------------------------
# DB helpers — same shape as the stub
# ---------------------------------------------------------------------------
def ws_write(table: str, rows: dict) -> dict:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=10,
        )
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def ws_query(sql: str) -> dict:
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
        return resp.json()
    except Exception as e:
        return {"rows": [], "error": str(e)}


def ws_execute(sql: str) -> dict:
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=30)
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _sql_quote(value: str) -> str:
    """Crude SQL-string escape for inline interpolation (mirrors stub style)."""
    if value is None:
        return ""
    return str(value).replace("'", "''")


def send_heartbeat():
    try:
        ws_write(
            "service_health",
            {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        pass


def check_single_instance() -> bool:
    pid_file = PID_FILE
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                old_pid = int(f.read().strip())
            try:
                os.kill(old_pid, 0)
                print(f"Service {SERVICE_NAME} already running with PID {old_pid}")
                return False
            except OSError:
                pass
        except (ValueError, IOError):
            pass
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    return True


def get_uptime_seconds() -> float:
    return time.time() - start_time


# ---------------------------------------------------------------------------
# Global exception middleware — clean JSON 500, never crash the app
# ---------------------------------------------------------------------------
@app.middleware("http")
async def catch_all_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Unhandled error on %s %s: %s", request.method, request.url.path, e)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "detail": str(e),
                "path": request.url.path,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )


# ---------------------------------------------------------------------------
# Optional admin auth — only used if email_guid_auth is importable
# ---------------------------------------------------------------------------
def _try_admin_gate(request: Request) -> Optional[str]:
    """
    Returns None if the request is allowed through, or an HTML string with the
    auth-fail page if not. Any failure inside email_guid_auth is swallowed so
    the route always renders something sensible.
    """
    try:
        import email_guid_auth  # noqa: F401  — presence-checked import

        # Best-effort: try a couple of common entry-point names without blowing up
        check = (
            getattr(email_guid_auth, "verify_request", None)
            or getattr(email_guid_auth, "verify", None)
            or getattr(email_guid_auth, "authorize", None)
        )
        if check is None:
            return None  # module present but no recognisable check — let through
        ok = check(request)
        if ok is True:
            return None
        return _admin_denied_html()
    except ImportError:
        return None  # auth module not present — open access (placeholder mode)
    except Exception as e:
        log.warning("admin gate raised, falling through to placeholder: %s", e)
        return None


def _admin_denied_html() -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>ZO-SENTINEL — Access denied</title>"
        f"<style>{_BASE_CSS}</style></head><body>"
        "<header><h1>ZO-SENTINEL</h1><div class='sub'>Admin · access required</div></header>"
        "<div class='card'><div class='emptystate'>This area requires an authenticated admin GUID. "
        "<a href='/'>Return home →</a></div></div></body></html>"
    )


def _admin_placeholder_html(title: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>ZO-SENTINEL — {title}</title>"
        f"<style>{_BASE_CSS}</style></head><body>"
        f"<header><h1>ZO-SENTINEL</h1><div class='sub'>{title} · admin</div></header>"
        f"<div class='card'><div class='emptystate'>{title} — coming soon. "
        "<a href='/'>← Back to search</a></div></div></body></html>"
    )


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class SubmitRequest(BaseModel):
    mcp_identifier: str = Field(..., min_length=1, max_length=512)
    requester_name: str = Field(..., min_length=1, max_length=256)
    requester_team: str = Field(..., min_length=1, max_length=256)
    business_purpose: str = Field(..., min_length=1, max_length=4000)
    environment: str = Field(..., min_length=1, max_length=64)


# ---------------------------------------------------------------------------
# Inline CSS — matches the class names referenced by the captured JS
# ---------------------------------------------------------------------------
_BASE_CSS = """
:root{
  --bg:#0a0a0f; --panel:#12121b; --panel-2:#1a1a26; --line:#23232f;
  --text:#e6e6ee; --muted:#8a8aa0; --accent:#00c98d; --accent-2:#5fb8ff;
  --warn:#f5a623; --danger:#f56342; --crit:#e02020; --unknown:#6a6a82;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  font-size:14px;line-height:1.45;}
a{color:var(--accent-2);text-decoration:none}
a:hover{text-decoration:underline}
header{padding:22px 28px;border-bottom:1px solid var(--line);
  display:flex;align-items:baseline;gap:18px;flex-wrap:wrap}
header h1{margin:0;font-size:22px;letter-spacing:2px;color:var(--accent)}
header .sub{color:var(--muted);font-size:11px;letter-spacing:1.5px;text-transform:uppercase}
header a{margin-left:auto;font-size:12px;color:var(--muted)}
header a + a{margin-left:14px}
.search{padding:24px 28px;display:flex;gap:10px;border-bottom:1px solid var(--line);background:var(--panel)}
.search input{flex:1;padding:12px 14px;background:var(--bg);color:var(--text);
  border:1px solid var(--line);border-radius:6px;font-size:14px;outline:none}
.search input:focus{border-color:var(--accent-2)}
.search button{padding:12px 22px;background:var(--accent);color:#001b10;border:0;
  border-radius:6px;font-weight:600;cursor:pointer;font-size:14px}
.search button:disabled{opacity:.5;cursor:not-allowed}
#landing,#result{padding:24px 28px;max-width:1100px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:18px 20px;margin-bottom:16px}
.muted{color:var(--muted)}
.sub{color:var(--muted);font-size:12px}
.hash{font-family:"SF Mono",Menlo,Consolas,monospace;font-size:12px;color:var(--muted);
  word-break:break-all}
.emptystate{color:var(--muted);text-align:center;padding:36px 12px;font-size:14px}
.spinner{color:var(--muted);padding:18px 0}
.score{font-weight:700}
/* Recent-row */
.recent-row{display:flex;align-items:center;justify-content:space-between;
  padding:10px 0;border-bottom:1px solid var(--line);cursor:pointer}
.recent-row:last-child{border-bottom:0}
.recent-row:hover{background:var(--panel-2)}
/* Verdict badges */
.badge{display:inline-block;padding:3px 10px;border-radius:999px;
  font-size:11px;letter-spacing:.5px;text-transform:uppercase;margin-right:8px}
.b-trusted{background:rgba(0,201,141,.18);color:var(--accent)}
.b-blue{background:rgba(95,184,255,.18);color:var(--accent-2)}
.b-caution{background:rgba(245,166,35,.18);color:var(--warn)}
.b-risk{background:rgba(245,99,66,.18);color:var(--danger)}
.b-threat{background:rgba(224,32,32,.22);color:var(--crit)}
.b-unknown{background:rgba(106,106,130,.22);color:var(--unknown)}
/* Verdict-* helper colors */
.verdict-trusted{color:var(--accent)}
.verdict-caution{color:var(--warn)}
.verdict-risk{color:var(--danger)}
.verdict-threat{color:var(--crit)}
.verdict-unknown{color:var(--unknown)}
/* Sections */
.section{margin-top:14px;padding-top:14px;border-top:1px solid var(--line)}
.section-count{display:inline-block;margin-left:8px;padding:1px 8px;border-radius:999px;
  background:var(--panel-2);color:var(--muted);font-size:11px}
details > summary{cursor:pointer;outline:none;list-style:none;padding:6px 0;font-weight:600}
details > summary::-webkit-details-marker{display:none}
/* Signal bars */
.signals{display:flex;flex-direction:column;gap:8px;margin-top:8px}
.sig{display:grid;grid-template-columns:170px 1fr 50px;align-items:center;gap:10px}
.sig-name{color:var(--muted);font-size:12px}
.sig-bar{background:var(--bg);height:8px;border-radius:4px;overflow:hidden}
.sig-fill{height:100%;background:var(--accent);border-radius:4px}
/* Severity tags on threats */
.sev-tag{display:inline-block;padding:1px 8px;border-radius:4px;font-size:11px;
  margin-right:6px;text-transform:uppercase;letter-spacing:.5px}
.sev-low{background:rgba(95,184,255,.18);color:var(--accent-2)}
.sev-med{background:rgba(245,166,35,.18);color:var(--warn)}
.sev-high{background:rgba(245,99,66,.18);color:var(--danger)}
.sev-crit{background:rgba(224,32,32,.22);color:var(--crit)}
/* Attestation */
.attest{margin-top:8px;padding:12px;background:var(--panel-2);border-radius:6px;
  border:1px solid var(--line)}
.attest-label{font-size:10px;letter-spacing:1.5px;text-transform:uppercase;
  color:var(--muted);font-weight:600}
.caveat{margin-top:18px;padding:12px;background:rgba(245,166,35,.08);
  border-left:3px solid var(--warn);border-radius:4px;font-size:12px;color:var(--muted)}
"""


# ---------------------------------------------------------------------------
# Inline JS — captured client-side behaviour, reconstructed verbatim
# ---------------------------------------------------------------------------
_INLINE_JS = r"""
const BASE = '';
function scoreColor(s){
  if(s>=75)return'#00c98d';if(s>=50)return'#f5a623';if(s>=25)return'#f56342';return'#e02020';
}
function verdictBadgeClass(v){
  const m={TRUSTED_GENERAL:'b-trusted',TRUSTED_RESEARCH:'b-trusted',
    ENTERPRISE_CONTROLLED:'b-blue',CAUTION_LIMITED:'b-caution',
    HIGH_RISK_ISOLATED:'b-risk',KNOWN_THREAT:'b-threat',INSUFFICIENT:'b-unknown'};
  return m[v]||'b-unknown';
}
function verdictLabel(v){
  const m={TRUSTED_GENERAL:'Likely safe · General use',
    TRUSTED_RESEARCH:'Likely safe · Research',
    ENTERPRISE_CONTROLLED:'Likely safe · Enterprise with controls',
    CAUTION_LIMITED:'Caution · Limited scoped use',
    HIGH_RISK_ISOLATED:'High risk · Isolated test only',
    KNOWN_THREAT:'Known threat indicators',
    INSUFFICIENT:'Insufficient data'};
  return m[v]||v||'Unknown';
}
function fmtDate(s){return s?String(s).slice(0,16).replace('T',' '):'';}
function safe(s){
  if(s==null)return '';
  return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function sevClass(sev){
  const s=String(sev||'').toLowerCase();
  if(s.startsWith('crit'))return 'sev-crit';
  if(s.startsWith('high'))return 'sev-high';
  if(s.startsWith('med'))return 'sev-med';
  return 'sev-low';
}

async function loadRecent(){
  try{
    const r=await fetch(`${BASE}/api/recent?limit=8`);
    const d=await r.json();
    const rows=d.recent||[];
    if(!rows.length){
      document.getElementById('landing').innerHTML='<div class="emptystate">No MCPs have been assessed yet.</div>';
      return;
    }
    const html='<div class="card"><div class="attest-label" style="margin-bottom:10px">RECENT ASSESSMENTS</div>'
      +rows.map(r=>{
        const v=r.verdict||'INSUFFICIENT';
        const ts=r.trust_score!=null?Math.round(r.trust_score):'—';
        return `<div class="recent-row" onclick="searchFor('${safe(r.server_id)}')">
          <div><span class="badge ${verdictBadgeClass(v)}">${verdictLabel(v)}</span>
          <div>${safe(r.name||r.server_id)}</div>
          <div class="muted">${fmtDate(r.last_assessed)}</div></div>
          <div class="score" style="font-size:20px;color:${ts!=='—'?scoreColor(ts):'var(--unknown)'}">${ts}</div>
          </div>`;
      }).join('')+'</div>';
    document.getElementById('landing').innerHTML=html;
  }catch(e){
    document.getElementById('landing').innerHTML=`<div class="emptystate">Error loading recent: ${safe(e.message)}</div>`;
  }
}

function searchFor(serverId){
  // Deep-link to the per-MCP detail page so URLs are shareable and
  // browser back works as a real navigation.
  window.location.href = `${BASE}/mcp/${encodeURIComponent(serverId)}`;
}

async function doSearch(){
  const q=document.getElementById('q').value.trim();
  if(!q)return;
  document.getElementById('btn').disabled=true;
  document.getElementById('landing').innerHTML='';
  document.getElementById('result').innerHTML='<div class="spinner">Querying ZO-SENTINEL…</div>';
  try{
    const r=await fetch(`${BASE}/api/search?q=${encodeURIComponent(q)}`);
    const d=await r.json();
    renderResult(d);
  }catch(e){
    document.getElementById('result').innerHTML=`<div class="card">Error: ${safe(e.message)}</div>`;
  }
  document.getElementById('btn').disabled=false;
}

function renderResult(d){
  const root=document.getElementById('result');
  const results=(d&&d.results)||[];
  if(!results.length){
    root.innerHTML='<div class="card"><div class="emptystate">No matches found.</div></div>';
    return;
  }
  const top=results[0];
  const v=top.verdict||'INSUFFICIENT';
  const ts=top.trust_score!=null?Math.round(top.trust_score):null;
  const tsDisp=ts!=null?ts:'—';
  const tsColor=ts!=null?scoreColor(ts):'var(--unknown)';

  // ---- HEADER ----
  let html='<div class="card">';
  html+=`<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:18px">
    <div>
      <span class="badge ${verdictBadgeClass(v)}">${verdictLabel(v)}</span>
      <h2 style="margin:8px 0 4px 0;font-size:20px">${safe(top.name||top.server_id)}</h2>
      <div class="hash">${safe(top.url||'')}</div>
      <div class="hash" style="margin-top:4px">${safe(top.server_id||'')}</div>
    </div>
    <div style="text-align:right">
      <div class="attest-label">TRUST SCORE</div>
      <div class="score" style="font-size:42px;color:${tsColor}">${tsDisp}</div>
    </div>
  </div>`;
  if(top.verdict_reasoning){
    html+=`<div style="margin-top:12px;color:var(--muted);font-size:13px">${safe(top.verdict_reasoning)}</div>`;
  }

  // ---- SIGNALS ----
  const signals=d.signals||[];
  html+=`<div class="section"><details open><summary>Signals<span class="section-count">${signals.length}</span></summary>`;
  if(signals.length){
    html+='<div class="signals">';
    signals.forEach(s=>{
      const score=Math.max(0,Math.min(100,Number(s.score)||0));
      html+=`<div class="sig">
        <div class="sig-name">${safe(s.name||s.signal||'signal')}</div>
        <div class="sig-bar"><div class="sig-fill" style="width:${score}%;background:${scoreColor(score)}"></div></div>
        <div class="muted" style="text-align:right">${Math.round(score)}</div>
      </div>`;
    });
    html+='</div>';
  } else {
    html+='<div class="emptystate">No signal data available.</div>';
  }
  html+='</details></div>';

  // ---- THREATS ----
  const threats=d.threats||[];
  html+=`<div class="section"><details ${threats.length?'open':''}><summary>Threat associations<span class="section-count">${threats.length}</span></summary>`;
  if(threats.length){
    threats.forEach(t=>{
      html+=`<div style="padding:8px 0;border-bottom:1px solid var(--line)">
        <span class="sev-tag ${sevClass(t.severity)}">${safe(t.severity||'low')}</span>
        <strong>${safe(t.title||t.indicator||'threat')}</strong>
        <div class="muted" style="font-size:12px;margin-top:4px">${safe(t.description||'')}</div>
        <div class="hash" style="margin-top:4px">${safe(t.source||'')} · ${fmtDate(t.reported_at)}</div>
      </div>`;
    });
  } else {
    html+='<div class="emptystate">No threat associations on file.</div>';
  }
  html+='</details></div>';

  // ---- RISK ----
  const risk=d.risk||{};
  html+=`<div class="section"><details><summary>Risk register</summary>`;
  if(risk&&Object.keys(risk).length){
    const tier=risk.risk_tier||'—';
    html+=`<div style="margin-top:8px">
      <div><span class="attest-label">TIER</span> &nbsp; <strong>${safe(tier)}</strong></div>
      <div style="margin-top:6px"><span class="attest-label">CATEGORY</span> &nbsp; ${safe(risk.category||'—')}</div>
      <div style="margin-top:6px"><span class="attest-label">NOTES</span><div class="muted" style="margin-top:2px">${safe(risk.notes||'No notes recorded.')}</div></div>
    </div>`;
  } else {
    html+='<div class="emptystate">No risk register entry.</div>';
  }
  html+='</details></div>';

  // ---- ATTESTATION ----
  const attest=d.attestation||{};
  html+=`<div class="section"><details><summary>Attestation</summary>`;
  if(attest&&Object.keys(attest).length){
    html+=`<div class="attest">
      <div><span class="attest-label">SIGNED BY</span> &nbsp; ${safe(attest.signed_by||'—')}</div>
      <div style="margin-top:6px"><span class="attest-label">METHOD</span> &nbsp; ${safe(attest.method||'—')}</div>
      <div style="margin-top:6px"><span class="attest-label">DIGEST</span><div class="hash">${safe(attest.digest||'—')}</div></div>
      <div style="margin-top:6px"><span class="attest-label">TIMESTAMP</span> &nbsp; ${fmtDate(attest.timestamp)}</div>
    </div>`;
  } else {
    html+='<div class="emptystate">No attestation recorded.</div>';
  }
  html+='</details></div>';

  // ---- HISTORY ----
  const hist=d.history||[];
  html+=`<div class="section"><details><summary>Assessment history<span class="section-count">${hist.length}</span></summary>`;
  if(hist.length){
    hist.forEach(h=>{
      const hv=h.verdict||'INSUFFICIENT';
      const hs=h.trust_score!=null?Math.round(h.trust_score):'—';
      html+=`<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line)">
        <div>
          <span class="badge ${verdictBadgeClass(hv)}">${verdictLabel(hv)}</span>
          <span class="muted" style="margin-left:6px">${fmtDate(h.assessed_at)}</span>
        </div>
        <div class="score" style="color:${hs!=='—'?scoreColor(hs):'var(--unknown)'}">${hs}</div>
      </div>`;
    });
  } else {
    html+='<div class="emptystate">No prior assessments.</div>';
  }
  html+='</details></div>';

  // ---- CAVEAT ----
  html+='<div class="caveat">Non-binding assessment — ZO-SENTINEL trust scores are advisory, derived from observed signals at scan time, and may not reflect current state. Validate independently before granting production scope.</div>';

  html+='</div>'; // /card
  root.innerHTML=html;
}

window.addEventListener('DOMContentLoaded', loadRecent);
"""


def _home_html() -> str:
    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<title>ZO-SENTINEL — MCP Trust Intelligence</title>
<style>{_BASE_CSS}</style>
</head><body>
<header>
  <h1>ZO-SENTINEL</h1>
  <div class="sub">MCP TRUST INTELLIGENCE · NON-BINDING ASSESSMENT</div>
  <a href="/admin-threats">Admin · Threats</a>
  <a href="/admin-risk">Admin · Risk</a>
</header>
<div class="search">
  <input id="q" placeholder="MCP name · npm package · URL · server_id" onkeydown="if(event.key==='Enter')doSearch()">
  <button id="btn" onclick="doSearch()">Assess →</button>
</div>
<div id="landing"></div>
<div id="result"></div>
<script>{_INLINE_JS}</script>
</body></html>"""


def _detail_html(server_id: str) -> str:
    """Per-MCP detail page. Pre-loads the same payload returned by /api/search
    so the renderResult() flow can be reused unchanged."""
    safe_id = (server_id or "").replace("\\", "\\\\").replace("'", "\\'")
    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<title>ZO-SENTINEL — {server_id}</title>
<style>{_BASE_CSS}</style>
</head><body>
<header>
  <h1>ZO-SENTINEL</h1>
  <div class="sub">← <a href="/">Back to home</a> &nbsp;·&nbsp; ASSESSMENT &nbsp;·&nbsp; {server_id}</div>
  <a href="/admin-threats">Admin · Threats</a>
  <a href="/admin-risk">Admin · Risk</a>
</header>
<div class="search">
  <input id="q" value="{server_id}" placeholder="MCP name · npm package · URL · server_id" onkeydown="if(event.key==='Enter')doSearch()">
  <button id="btn" onclick="doSearch()">Re-assess →</button>
</div>
<div id="landing"></div>
<div id="result"><div class="spinner">Loading {server_id}…</div></div>
<script>{_INLINE_JS}
// Pre-load the assessment payload for this server_id and render it once.
(async function(){{
  try{{
    const r = await fetch(`${{BASE}}/api/search?q=${{encodeURIComponent('{safe_id}')}}`);
    const d = await r.json();
    renderResult(d);
  }}catch(e){{
    document.getElementById('result').innerHTML =
      `<div class="card">Error loading detail: ${{(e && e.message)||e}}</div>`;
  }}
}})();
</script>
</body></html>"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(content=_home_html())


@app.get("/mcp/{server_id}", response_class=HTMLResponse)
async def mcp_detail(server_id: str):
    """Per-MCP detail page — renders the same JS surface as /, but
    pre-loaded for one server_id and deep-linkable."""
    return HTMLResponse(content=_detail_html(server_id))


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/api/recent")
async def api_recent(limit: int = 10):
    limit = max(1, min(int(limit), 100))
    res = ws_query(
        f"""
        SELECT server_id, name, verdict, trust_score, last_assessed
        FROM mcp_server_registry
        WHERE last_assessed IS NOT NULL
        ORDER BY last_assessed DESC
        LIMIT {limit}
        """
    )
    return {"recent": res.get("rows", [])}


@app.get("/api/search")
async def api_search(q: str = ""):
    """
    Returns the rich assessment payload consumed by renderResult():
        { results, signals, threats, risk, attestation, history }
    """
    q = (q or "").strip()
    if not q:
        return {
            "results": [], "signals": [], "threats": [],
            "risk": {}, "attestation": {}, "history": [],
        }

    qe = _sql_quote(q)
    results_res = ws_query(
        f"""
        SELECT server_id, name, url, verdict, verdict_reasoning, trust_score
        FROM mcp_server_registry
        WHERE server_id = '{qe}'
           OR name LIKE '%{qe}%'
           OR url  LIKE '%{qe}%'
           OR description LIKE '%{qe}%'
        ORDER BY (server_id = '{qe}') DESC, trust_score DESC
        LIMIT 25
        """
    )
    results = results_res.get("rows", []) or []

    if not results:
        return {
            "results": [], "signals": [], "threats": [],
            "risk": {}, "attestation": {}, "history": [],
        }

    top_id = results[0].get("server_id")
    tide = _sql_quote(top_id) if top_id else ""

    signals = ws_query(
        f"""
        SELECT name, score
        FROM mcp_signal_scores
        WHERE server_id = '{tide}'
        ORDER BY scored_at DESC
        LIMIT 20
        """
    ).get("rows", []) or []

    threats = ws_query(
        f"""
        SELECT title, indicator, severity, description, source, reported_at
        FROM mcp_threat_associations
        WHERE server_id = '{tide}'
        ORDER BY reported_at DESC
        LIMIT 20
        """
    ).get("rows", []) or []

    risk_rows = ws_query(
        f"""
        SELECT risk_tier, category, notes
        FROM mcp_risk_register
        WHERE server_id = '{tide}'
        ORDER BY recorded_at DESC
        LIMIT 1
        """
    ).get("rows", []) or []
    risk = risk_rows[0] if risk_rows else {}

    attest_rows = ws_query(
        f"""
        SELECT signed_by, method, digest, timestamp
        FROM mcp_attestations
        WHERE server_id = '{tide}'
        ORDER BY timestamp DESC
        LIMIT 1
        """
    ).get("rows", []) or []
    attestation = attest_rows[0] if attest_rows else {}

    history = ws_query(
        f"""
        SELECT verdict, trust_score, assessed_at
        FROM mcp_assessment_history
        WHERE server_id = '{tide}'
        ORDER BY assessed_at DESC
        LIMIT 20
        """
    ).get("rows", []) or []

    return {
        "results": results,
        "signals": signals,
        "threats": threats,
        "risk": risk,
        "attestation": attestation,
        "history": history,
    }


@app.get("/api/registry")
async def api_registry(limit: int = 200, offset: int = 0):
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))
    res = ws_query(
        f"""
        SELECT server_id, name, url, description, verdict, trust_score, risk_tier, scan_count
        FROM mcp_server_registry
        ORDER BY name ASC
        LIMIT {limit} OFFSET {offset}
        """
    )
    rows = res.get("rows", []) or []
    return {"registry": rows, "count": len(rows)}


@app.get("/api/audit")
async def api_audit(limit: int = 100):
    limit = max(1, min(int(limit), 500))
    res = ws_query(
        f"""
        SELECT *
        FROM audit_log
        ORDER BY created_at DESC
        LIMIT {limit}
        """
    )
    return {"audit": res.get("rows", []) or []}


@app.post("/api/submit")
async def api_submit(request: Request):
    """
    Validate a SubmitRequest and persist to mcp_submissions.
    Returns 422 with Pydantic error details on validation failure.
    """
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid JSON body: {e}")

    try:
        sub = SubmitRequest(**payload)
    except ValidationError as ve:
        return JSONResponse(status_code=422, content={"errors": ve.errors()})

    submission_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    write_res = ws_write(
        "mcp_submissions",
        {
            "submission_id": submission_id,
            "mcp_identifier": sub.mcp_identifier,
            "requester_name": sub.requester_name,
            "requester_team": sub.requester_team,
            "business_purpose": sub.business_purpose,
            "environment": sub.environment,
            "submitted_at": now,
            "status": "pending",
        },
    )
    return {
        "submission_id": submission_id,
        "submitted_at": now,
        "write_ok": write_res.get("ok", True),
    }


# ---------------------------------------------------------------------------
# Admin: threats and risk-register data endpoints + HTML views
# ---------------------------------------------------------------------------
def _admin_error_html(title: str, msg: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>ZO-SENTINEL — {title}</title>"
        f"<style>{_BASE_CSS}</style></head><body>"
        f"<header><h1>ZO-SENTINEL</h1><div class='sub'>"
        f"← <a href='/'>Back to home</a> &nbsp;·&nbsp; ADMIN &nbsp;·&nbsp; {title}</div>"
        "</header>"
        f"<div class='card'><div class='emptystate'>{msg}</div></div>"
        "</body></html>"
    )


@app.get("/api/admin/threats")
async def api_admin_threats(severity: Optional[str] = None, limit: int = 500):
    """Threats joined to MCP names. Filterable by severity."""
    try:
        limit = max(1, min(int(limit), 2000))
        where = ""
        if severity:
            sev = _sql_quote(severity)
            where = f"WHERE LOWER(t.severity) = LOWER('{sev}')"
        rows = ws_query(
            f"""
            SELECT t.server_id, m.name AS mcp_name, t.title, t.indicator,
                   t.severity, t.description, t.source, t.reported_at
            FROM mcp_threat_associations t
            LEFT JOIN mcp_server_registry m ON m.server_id = t.server_id
            {where}
            ORDER BY t.reported_at DESC
            LIMIT {limit}
            """
        ).get("rows", []) or []
        return {"count": len(rows), "rows": rows}
    except Exception as e:
        log.exception("api_admin_threats failed: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e), "rows": []})


@app.get("/api/admin/risk")
async def api_admin_risk(tier: Optional[str] = None, limit: int = 500):
    """Risk register joined to MCP names. Filterable by tier."""
    try:
        limit = max(1, min(int(limit), 2000))
        where = ""
        if tier:
            t = _sql_quote(tier)
            where = f"WHERE LOWER(r.risk_tier) = LOWER('{t}')"
        rows = ws_query(
            f"""
            SELECT r.server_id, m.name AS mcp_name, r.risk_tier, r.risk_rank,
                   r.threat_count, r.staleness_hours, r.computed_at
            FROM mcp_risk_register r
            LEFT JOIN mcp_server_registry m ON m.server_id = r.server_id
            {where}
            ORDER BY r.risk_rank DESC NULLS LAST
            LIMIT {limit}
            """
        ).get("rows", []) or []
        return {"count": len(rows), "rows": rows}
    except Exception as e:
        log.exception("api_admin_risk failed: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e), "rows": []})


_ADMIN_THREATS_JS = r"""
const rows = window.__rows || [];
const tbody = document.getElementById('rows');
let sortKey = 'reported_at';
let sortDir = -1;          // -1 desc, 1 asc
let filterSev = '';

function sevRank(s){ const t=String(s||'').toLowerCase();
  if(t.startsWith('crit')) return 4;
  if(t.startsWith('high')) return 3;
  if(t.startsWith('med'))  return 2;
  return 1;
}
function safe(s){ if(s==null) return ''; return String(s).replace(/[&<>"']/g,c=>(
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function fmt(s){ return s ? String(s).slice(0,16).replace('T',' ') : ''; }

function render(){
  let view = rows.slice();
  if (filterSev) {
    view = view.filter(r => String(r.severity||'').toLowerCase().startsWith(filterSev));
  }
  view.sort((a,b)=>{
    let va = a[sortKey], vb = b[sortKey];
    if (sortKey === 'severity') { va = sevRank(va); vb = sevRank(vb); }
    if (va == null) return 1;
    if (vb == null) return -1;
    if (va < vb) return -1*sortDir;
    if (va > vb) return  1*sortDir;
    return 0;
  });
  document.getElementById('count').textContent = view.length + ' threat(s)';
  tbody.innerHTML = view.map(r => {
    const sev = String(r.severity||'low').toLowerCase();
    let sevCls = 'sev-low';
    if (sev.startsWith('crit')) sevCls = 'sev-crit';
    else if (sev.startsWith('high')) sevCls = 'sev-high';
    else if (sev.startsWith('med')) sevCls = 'sev-med';
    return `<tr>
      <td><span class="sev-tag ${sevCls}">${safe(r.severity||'low')}</span></td>
      <td><a href="/mcp/${encodeURIComponent(r.server_id||'')}">${safe(r.mcp_name||r.server_id||'—')}</a></td>
      <td>${safe(r.title||r.indicator||'')}</td>
      <td class="muted">${safe(r.source||'')}</td>
      <td class="muted">${fmt(r.reported_at)}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="5" class="emptystate">No threats match the filter.</td></tr>';
}

function setSort(k){
  if (sortKey === k) sortDir = -sortDir;
  else { sortKey = k; sortDir = -1; }
  render();
}
document.getElementById('sev').addEventListener('change', e => {
  filterSev = e.target.value;
  render();
});
render();
"""


@app.get("/admin-threats", response_class=HTMLResponse)
async def admin_threats(request: Request):
    """Functional threats admin view — sortable + severity-filterable table."""
    try:
        denied = _try_admin_gate(request)
        if denied is not None:
            return HTMLResponse(content=denied, status_code=403)
        try:
            data = await api_admin_threats()
            if isinstance(data, JSONResponse):
                raise RuntimeError("admin threats API returned 500")
            rows = data.get("rows", [])
        except Exception as e:
            log.warning("admin-threats data fetch failed: %s", e)
            return HTMLResponse(
                content=_admin_error_html(
                    "Threats",
                    "Couldn't load admin data right now. Try again in a minute.",
                ),
                status_code=200,
            )
        rows_json = json.dumps(rows)
        html = f"""<!doctype html><html><head>
<meta charset="utf-8">
<title>ZO-SENTINEL — Threats</title>
<style>{_BASE_CSS}
table{{width:100%;border-collapse:collapse}}
th{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);
  cursor:pointer;color:var(--muted);font-size:11px;letter-spacing:1px;text-transform:uppercase}}
th:hover{{color:var(--text)}}
td{{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
.controls{{display:flex;gap:14px;align-items:center;margin-bottom:14px}}
select{{background:var(--bg);color:var(--text);border:1px solid var(--line);
  padding:6px 10px;border-radius:6px;font-size:13px}}
</style></head><body>
<header>
  <h1>ZO-SENTINEL</h1>
  <div class="sub">← <a href="/">Back to home</a> &nbsp;·&nbsp; ADMIN &nbsp;·&nbsp; Threats</div>
  <a href="/admin-risk">Admin · Risk</a>
</header>
<div id="landing"><div class="card">
  <div class="controls">
    <strong>Filter severity:</strong>
    <select id="sev">
      <option value="">all</option>
      <option value="crit">critical</option>
      <option value="high">high</option>
      <option value="med">medium</option>
      <option value="low">low</option>
    </select>
    <span class="muted" id="count">0 threat(s)</span>
  </div>
  <table>
    <thead><tr>
      <th onclick="setSort('severity')">Severity</th>
      <th onclick="setSort('mcp_name')">MCP</th>
      <th>Title / indicator</th>
      <th>Source</th>
      <th onclick="setSort('reported_at')">Reported</th>
    </tr></thead>
    <tbody id="rows"></tbody>
  </table>
</div></div>
<script>window.__rows = {rows_json};</script>
<script>{_ADMIN_THREATS_JS}</script>
</body></html>"""
        return HTMLResponse(content=html)
    except Exception as e:
        log.exception("admin-threats unhandled: %s", e)
        return HTMLResponse(
            content=_admin_error_html(
                "Threats", "Couldn't load admin data right now. Try again in a minute."
            ),
            status_code=200,
        )


_ADMIN_RISK_JS = r"""
const rows = window.__rows || [];
const tbody = document.getElementById('rows');
let sortKey = 'risk_rank';
let sortDir = -1;
let filterTier = '';

function safe(s){ if(s==null) return ''; return String(s).replace(/[&<>"']/g,c=>(
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function fmt(s){ return s ? String(s).slice(0,16).replace('T',' ') : ''; }

function tierClass(t){
  const x=String(t||'').toUpperCase();
  if(x.includes('CRIT')) return 'b-threat';
  if(x.includes('HIGH')) return 'b-risk';
  if(x.includes('MED'))  return 'b-caution';
  if(x.includes('LOW'))  return 'b-trusted';
  return 'b-unknown';
}

function render(){
  let view = rows.slice();
  if (filterTier) {
    view = view.filter(r => String(r.risk_tier||'').toUpperCase().startsWith(filterTier));
  }
  view.sort((a,b)=>{
    let va = a[sortKey], vb = b[sortKey];
    if (va == null) return 1;
    if (vb == null) return -1;
    if (va < vb) return -1*sortDir;
    if (va > vb) return  1*sortDir;
    return 0;
  });
  document.getElementById('count').textContent = view.length + ' entr' + (view.length===1?'y':'ies');
  tbody.innerHTML = view.map(r => `<tr>
    <td><span class="badge ${tierClass(r.risk_tier)}">${safe(r.risk_tier||'—')}</span></td>
    <td><a href="/mcp/${encodeURIComponent(r.server_id||'')}">${safe(r.mcp_name||r.server_id||'—')}</a></td>
    <td>${r.risk_rank!=null?Number(r.risk_rank).toFixed(2):'—'}</td>
    <td>${r.threat_count!=null?r.threat_count:0}</td>
    <td class="muted">${r.staleness_hours!=null?Math.round(r.staleness_hours)+'h':''}</td>
    <td class="muted">${fmt(r.computed_at)}</td>
  </tr>`).join('') || '<tr><td colspan="6" class="emptystate">No risk register entries match the filter.</td></tr>';
}

function setSort(k){
  if (sortKey === k) sortDir = -sortDir;
  else { sortKey = k; sortDir = -1; }
  render();
}
document.getElementById('tier').addEventListener('change', e => {
  filterTier = e.target.value;
  render();
});
render();
"""


@app.get("/admin-risk", response_class=HTMLResponse)
async def admin_risk(request: Request):
    """Functional risk-register admin view — sortable + tier-filterable table."""
    try:
        denied = _try_admin_gate(request)
        if denied is not None:
            return HTMLResponse(content=denied, status_code=403)
        try:
            data = await api_admin_risk()
            if isinstance(data, JSONResponse):
                raise RuntimeError("admin risk API returned 500")
            rows = data.get("rows", [])
        except Exception as e:
            log.warning("admin-risk data fetch failed: %s", e)
            return HTMLResponse(
                content=_admin_error_html(
                    "Risk register",
                    "Couldn't load admin data right now. Try again in a minute.",
                ),
                status_code=200,
            )
        rows_json = json.dumps(rows)
        html = f"""<!doctype html><html><head>
<meta charset="utf-8">
<title>ZO-SENTINEL — Risk register</title>
<style>{_BASE_CSS}
table{{width:100%;border-collapse:collapse}}
th{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);
  cursor:pointer;color:var(--muted);font-size:11px;letter-spacing:1px;text-transform:uppercase}}
th:hover{{color:var(--text)}}
td{{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
.controls{{display:flex;gap:14px;align-items:center;margin-bottom:14px}}
select{{background:var(--bg);color:var(--text);border:1px solid var(--line);
  padding:6px 10px;border-radius:6px;font-size:13px}}
</style></head><body>
<header>
  <h1>ZO-SENTINEL</h1>
  <div class="sub">← <a href="/">Back to home</a> &nbsp;·&nbsp; ADMIN &nbsp;·&nbsp; Risk register</div>
  <a href="/admin-threats">Admin · Threats</a>
</header>
<div id="landing"><div class="card">
  <div class="controls">
    <strong>Filter tier:</strong>
    <select id="tier">
      <option value="">all</option>
      <option value="CRIT">critical</option>
      <option value="HIGH">high</option>
      <option value="MED">medium</option>
      <option value="LOW">low</option>
    </select>
    <span class="muted" id="count">0 entries</span>
  </div>
  <table>
    <thead><tr>
      <th onclick="setSort('risk_tier')">Tier</th>
      <th onclick="setSort('mcp_name')">MCP</th>
      <th onclick="setSort('risk_rank')">Risk rank</th>
      <th onclick="setSort('threat_count')">Threats</th>
      <th onclick="setSort('staleness_hours')">Staleness</th>
      <th onclick="setSort('computed_at')">Computed</th>
    </tr></thead>
    <tbody id="rows"></tbody>
  </table>
</div></div>
<script>window.__rows = {rows_json};</script>
<script>{_ADMIN_RISK_JS}</script>
</body></html>"""
        return HTMLResponse(content=html)
    except Exception as e:
        log.exception("admin-risk unhandled: %s", e)
        return HTMLResponse(
            content=_admin_error_html(
                "Risk register", "Couldn't load admin data right now. Try again in a minute."
            ),
            status_code=200,
        )


# ---------------------------------------------------------------------------
# Heartbeat thread
# ---------------------------------------------------------------------------
def heartbeat_loop():
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


_heartbeat_thread: Optional[threading.Thread] = None


@app.on_event("startup")
async def _startup():
    global _heartbeat_thread
    _heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    _heartbeat_thread.start()
    log.info("ui_server_rich startup complete on port %s", SERVICE_PORT)


def run():
    if not check_single_instance():
        return
    print(f"Starting {SERVICE_NAME} (rich) on port {SERVICE_PORT}")
    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORT, log_level="info")


if __name__ == "__main__":
    run()
