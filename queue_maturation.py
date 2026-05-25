#!/usr/bin/env python3
"""
Queue Phase 17-19 maturation directives + GUID email auth.
Fixes applied over the provided payload:
  - handler: 'generate_file' (not a model name)
  - priority: float 0.0-1.0 (not integers 1-5)
  - threat_intel -> mcp_threat_associations (correct table name)
  - Added GUID email auth directive
"""
import os, json, glob
from pathlib import Path

QUEUE_DIR = Path('/home/workspace/zo_sentinel/directives')
QUEUE_DIR.mkdir(exist_ok=True)


def get_existing_tasks():
    tasks = set()
    for fp in glob.glob(str(QUEUE_DIR / '*.json')):
        try: tasks.add(json.loads(Path(fp).read_text()).get('task',''))
        except Exception: pass
    return tasks

def next_seq():
    nums = []
    for fp in glob.glob(str(QUEUE_DIR / '*.json')):
        try: nums.append(int(Path(fp).name.split('_')[0]))
        except ValueError: pass
    return max(nums, default=119) + 1


NEW_DIRECTIVES = [
    {
        "task": "build_guid_auth_service",
        "output_file": "guid_auth_service.py",
        "complexity": "high", "phase": "17", "priority": 0.99,
        "reads": ["schema.py"],
        "description": (
            "GUID email authentication service daemon on port 8775. "
            "Creates and validates time-limited single-use GUIDs for admin authentication. "
            "Tables: CREATE TABLE IF NOT EXISTS admin_auth_tokens (token VARCHAR PRIMARY KEY, "
            "email VARCHAR, created_at TIMESTAMPTZ DEFAULT now(), "
            "expires_at TIMESTAMPTZ, used BOOLEAN DEFAULT FALSE, "
            "used_at TIMESTAMPTZ, purpose VARCHAR). All via write_service:8772. "
            "Routes: "
            "POST /auth/request: accepts {email, purpose}. Checks email against "
            "ADMIN_EMAILS env var (comma-separated whitelist). If authorised: "
            "generates uuid.uuid4() token, stores in admin_auth_tokens with "
            "expires_at=now()+1hour, sends email via POST https://api.zo.computer/zo/notify "
            "with subject 'ZO-SENTINEL Admin Access' and body containing clickable link "
            "http://localhost:8790/admin?token={guid}. Returns {sent: true, email: email}. "
            "POST /auth/validate: accepts {token}. Queries admin_auth_tokens WHERE token=? "
            "AND used=FALSE AND expires_at > now(). If valid: marks used=TRUE, used_at=now(), "
            "returns {valid: true, email: email, purpose: purpose}. "
            "If expired or used: returns 401 {valid: false, reason: expired|already_used}. "
            "GET /auth/status/{token}: returns token validity without consuming it. "
            "GET /health: standard health check. "
            "ADMIN_EMAILS=os.environ.get('ADMIN_EMAILS','robin.craib@gmail.com'). "
            "Single-use enforcement is critical: once validated the token cannot be reused. "
            "Tokens expire after 1 hour regardless of use. Heartbeat. run()+uvicorn."
        )
    },
    {
        "task": "build_manual_override_api",
        "output_file": "manual_override_api.py",
        "complexity": "high", "phase": "17", "priority": 0.97,
        "reads": ["schema.py", "guid_auth_service.py"],
        "description": (
            "Manual admin override API on port 8776. Human operator override for MCP trust scores. "
            "POST /api/override: accepts {mcp_name, new_trust_score, status, reason, admin_token}. "
            "Authentication: POST http://127.0.0.1:8775/auth/validate {token: admin_token}. "
            "If response {valid: false}: return 401 {error: unauthorised}. "
            "Idempotency: ws_query mcp_server_registry WHERE name=mcp_name -- if exact "
            "trust_score and status already match, return {status: already_set, skipped: true}. "
            "Execution: ws_write mcp_server_registry {server_id, trust_score, verdict, "
            "verdict_reasoning: reason, last_assessed: now}. "
            "ws_write mcp_risk_register {server_id, risk_tier: status, risk_rank: 100-new_trust_score}. "
            "ws_write audit_log {event_type: manual_override, actor: email from /auth/validate, "
            "target_server_id: server_id, action: override, outcome: success, "
            "details_json: json of all fields, timestamp: now}. "
            "POST /api/quarantine/{server_id}: shortcut to set trust_score=0, status=quarantined. "
            "GET /api/overrides: list recent manual overrides from audit_log. "
            "GET /health. All DB via write_service:8772. Heartbeat. run()+uvicorn."
        )
    },
    {
        "task": "build_advanced_filter_api",
        "output_file": "advanced_filter_api.py",
        "complexity": "high", "phase": "17", "priority": 0.96,
        "reads": ["schema.py", "db_utils.py"],
        "description": (
            "OLAP-style faceted filter API on port 8777. "
            "POST /api/discover: accepts JSON filter payload, constructs safe DuckDB SQL, "
            "returns matching MCPs. "
            "Supported filters: trust_score_lt (float), trust_score_gt (float), "
            "verdict (str or list), risk_tier (str or list), "
            "has_threats (bool -- servers in mcp_threat_associations), "
            "has_cve (bool -- threat_type containing 'cve'), "
            "registry_source (str), name_contains (str ILIKE), "
            "assessed_within_days (int), staleness_gt_days (int). "
            "Build WHERE clause dynamically: validate each key against allowlist, "
            "use parameterised queries (no f-string SQL injection). "
            "For has_threats=True: add EXISTS subquery on mcp_threat_associations. "
            "Route ALL queries through ws_query (POST 8772/query). NEVER duckdb.connect(). "
            "Returns {filters_applied, total, results: [...], query_time_ms}. "
            "GET /api/facets: returns distinct values for verdict, risk_tier, registry_source "
            "for building frontend filter dropdowns. "
            "GET /health. Heartbeat. run()+uvicorn."
        )
    },
    {
        "task": "build_compliance_export_service",
        "output_file": "compliance_export_service.py",
        "complexity": "high", "phase": "18", "priority": 0.95,
        "reads": ["schema.py"],
        "description": (
            "Compliance export service on port 8778. Ad-hoc ETL for enterprise reporting. "
            "GET /api/export/csv: full risk report as CSV download. "
            "Query: JOIN mcp_server_registry r ON r.server_id = rr.server_id "
            "LEFT JOIN mcp_risk_register rr ON r.server_id = rr.server_id "
            "LEFT JOIN mcp_threat_associations ta ON r.server_id = ta.server_id "
            "selecting: server_id, name, url, verdict, trust_score, risk_tier, "
            "threat_count (count of ta rows), has_critical (any severity=CRITICAL), "
            "last_assessed, first_seen. "
            "Use Python stdlib csv module: csv.DictWriter to StringIO, then return "
            "Response(content=buf.getvalue(), media_type='text/csv', "
            "headers={'Content-Disposition': 'attachment; filename=zo_mcp_risk_report.csv'}). "
            "GET /api/export/json: same data as JSON array. "
            "GET /api/export/summary: aggregate stats -- total servers, by verdict counts, "
            "by risk tier, critical threat count, average trust score. "
            "All DB via write_service:8772/query. No duckdb.connect(). "
            "GET /health. Heartbeat. run()+uvicorn."
        )
    },
    {
        "task": "build_forensic_detail_api",
        "output_file": "forensic_detail_api.py",
        "complexity": "high", "phase": "18", "priority": 0.94,
        "reads": ["schema.py", "audit_trail.py"],
        "description": (
            "Forensic detail API on port 8779. Full lifecycle drill-down for a single MCP. "
            "GET /api/forensics/{server_id_or_name}: "
            "1. Lookup: ws_query mcp_server_registry WHERE server_id=? OR name ILIKE ? LIMIT 1. "
            "If not found: return 404 {detail: 'MCP not found', searched: value}. "
            "2. Parallel data fetch (sequential ws_query calls): "
            "   - base record from mcp_server_registry "
            "   - all signals from mcp_signal_scores ORDER BY scored_at DESC "
            "   - all threats from mcp_threat_associations ORDER BY reported_at DESC "
            "   - risk register from mcp_risk_register "
            "   - latest attestation from mcp_attestations ORDER BY generated_at DESC LIMIT 1 "
            "   - audit history from audit_log WHERE target_server_id=? ORDER BY timestamp DESC "
            "   - definition changes from mcp_definition_history ORDER BY captured_at DESC "
            "3. Assemble into forensic timeline: chronological list of events with "
            "   event_type, timestamp, description, actor, data. "
            "   Timeline includes: first_seen, each signal score change, each threat added, "
            "   each attestation, each manual override from audit_log, verdict changes. "
            "4. Return {server_id, name, current_state: {...}, timeline: [...], "
            "   signal_history: [...], threat_history: [...], attestation: {...}}. "
            "GET /health. All DB via write_service:8772. No duckdb.connect(). Heartbeat. run()+uvicorn."
        )
    },
    {
        "task": "build_mcp_detail_view_ui",
        "output_file": "mcp_detail_view.html",
        "complexity": "medium", "phase": "19", "priority": 0.90,
        "reads": [],
        "description": (
            "Forensic drill-down HTML UI. Reads ?mcp= from URLSearchParams, "
            "fetches from http://127.0.0.1:8779/api/forensics/{mcp_name}. "
            "Design: brutalist dark (#090b0f background, #00d4ff cyan, monospace fonts, "
            "sharp borders no border-radius). "
            "Sections: (1) Header -- name, current verdict badge with colour, trust score large, "
            "last assessed date. (2) Signal breakdown -- horizontal bar chart per signal using "
            "CSS width%, colour green->orange->red by score. "
            "(3) Forensic timeline -- chronological list of events, each with timestamp, "
            "event_type badge, description. Scroll container max-height 400px. "
            "(4) Active threats -- severity-coloured list, evidence text truncated to 120 chars. "
            "(5) Current attestation box -- attestation_text, valid_until, caveats. "
            "Handle 404: show 'MCP not found' message with search link back to main UI. "
            "const params = new URLSearchParams(window.location.search); "
            "const mcp = params.get('mcp'); fetch relative path. "
            "Pure HTML+CSS+JS single file. No frameworks."
        )
    },
]


existing = get_existing_tasks()
seq = next_seq()
created = 0

for d in NEW_DIRECTIVES:
    if d['task'] in existing:
        print(f'  SKIP (exists): {d["task"]}')
        continue
    fname = str(seq).zfill(3) + '_' + d['task'] + '.json'
    fpath = QUEUE_DIR / fname
    payload = {
        'task':        d['task'],
        'handler':     'generate_file',
        'output_file': d['output_file'],
        'complexity':  d.get('complexity', 'high'),
        'phase':       d.get('phase', '17'),
        'priority':    float(d.get('priority', 0.95)),
        'reads':       d.get('reads', ['schema.py']),
        'description': d['description'],
        'from':        'maturation_phase_17_19'
    }
    fpath.write_text(json.dumps(payload, indent=2))
    print(f'  [OK] {fname}')
    existing.add(d['task'])
    seq += 1
    created += 1

print(f'\n{created} directives queued. Builder picks up next cycle.')