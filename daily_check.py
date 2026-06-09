#!/usr/bin/env python3
"""
daily_check.py v2.1 -- ZOMesh/ZO-SENTINEL Automated Daily Health Check

v2.1 (2026-04-23): Fixed check_gates -- was reading mesh_events for 'gate%'
  events but gates write heartbeats to service_health as 'gate_orchestrator'.
  Was always showing 290h (false positive). Now reads service_health correctly.
  rc=1 from gate runner = 'some gates failed' (EXPECTED). rc=2 = infra error.

v2.0 (2026-04-23): Added checks 11-13 (temporal lineage, new component health,
  pending deployments).

Run:
  python3 /home/workspace/zo_sentinel/daily_check.py
  python3 /home/workspace/zo_sentinel/daily_check.py --json
  python3 /home/workspace/zo_sentinel/daily_check.py --fix
"""
import os, sys, json, subprocess, requests, re
from datetime import datetime, timezone, timedelta
from pathlib import Path

WRITE_SERVICE = "http://127.0.0.1:8772"
MESH          = Path("/home/workspace/zo_mesh")
SENTINEL      = Path("/home/workspace/zo_sentinel")
LOGS          = Path("/home/workspace/logs")
JSON_MODE     = "--json" in sys.argv
AUTO_FIX      = "--fix"  in sys.argv

BUILDER_STALE_MINS = 30
BUILD_STALE_HOURS  = 6
DIRGEN_STALE_MINS  = 150
SVC_STALE_MINS     = 30
SVC_DOWN_MINS      = 120

results = []
critical_count = 0
warning_count  = 0


def ts():
    return datetime.now(timezone.utc).isoformat()

def ws_query(sql):
    try:
        r = requests.post(f"{WRITE_SERVICE}/query", json={"sql": sql}, timeout=8)
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception:
        pass
    return []

def ws_write(table, row):
    try:
        requests.post(f"{WRITE_SERVICE}/write",
                      json={"table": table, "rows": row, "wait": True}, timeout=8)
    except Exception:
        pass

def pgrep(pattern):
    try:
        r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
        return r.stdout.strip().split("\n") if r.returncode == 0 else []
    except Exception:
        return []

def http_check(url):
    try:
        return requests.get(url, timeout=5).status_code
    except Exception:
        return 0

def record(name, status, detail="", value=None):
    global critical_count, warning_count
    entry = {"check": name, "status": status, "detail": detail, "ts": ts()}
    if value is not None:
        entry["value"] = value
    results.append(entry)
    if status == "CRITICAL": critical_count += 1
    if status == "WARNING":  warning_count  += 1

def age_mins(iso_str):
    try:
        t = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - t).total_seconds() / 60
    except Exception:
        return 9999


# ── CHECK 1: Builder ──────────────────────────────────────────────────────────────────
def check_builder():
    pids = pgrep("goose_runner.py")  # the live Tier-1 builder (zo_sentinel_builder retired)
    if not pids:
        record("builder_process", "CRITICAL", "not running -- run zm go")
        return
    rows = ws_query("SELECT last_heartbeat FROM service_health WHERE service='goose_runner'")
    if not rows:
        record("builder_process", "WARNING", f"PID {pids[0]} running but no heartbeat")
        return
    age = age_mins(rows[0]["last_heartbeat"])
    record("builder_process", "WARNING" if age > BUILDER_STALE_MINS else "OK",
           f"PID {pids[0]}, heartbeat {age:.1f}m ago", value=round(age, 1))


# ── CHECK 2: MiniMax ──────────────────────────────────────────────────────────────────
def check_minimax():
    env_file = MESH / ".zo_env"
    if not env_file.exists():
        record("minimax_key", "WARNING", ".zo_env not found")
        return
    content = env_file.read_text()
    if "MINIMAX_API_KEY" in content and 'sk-' in content:
        log_path = LOGS / "goose_runner.log"
        if log_path.exists():
            tail = subprocess.run(["tail", "-200", str(log_path)],
                                  capture_output=True, text=True).stdout
            if "MiniMax: KEY SET" in tail or "minimax: raw=" in tail:
                record("minimax_key", "OK", "Key set and confirmed in builder log")
                return
        record("minimax_key", "WARNING", "Key in .zo_env but not confirmed in recent log")
    else:
        record("minimax_key", "CRITICAL", "MINIMAX_API_KEY not found in .zo_env")


# ── CHECK 3: Last build age ──────────────────────────────────────────────────────────
def check_last_build():
    rows = ws_query("""
        SELECT created_at FROM mesh_events
        WHERE agent_id='goose_tier1' AND event_type='DIRECTIVE_COMPLETE'
        ORDER BY created_at DESC LIMIT 1""")
    if not rows:
        record("last_build", "WARNING", "No build_complete events found")
        return
    hours = age_mins(rows[0]["created_at"]) / 60
    record("last_build", "WARNING" if hours > BUILD_STALE_HOURS else "OK",
           f"Last build {hours:.1f}h ago", value=round(hours, 1))


# ── CHECK 4: Recent builds 24h ─────────────────────────────────────────────────────
def check_recent_builds():
    rows = ws_query("""
        SELECT payload, created_at FROM mesh_events
        WHERE agent_id='goose_tier1' AND event_type='DIRECTIVE_COMPLETE'
          AND created_at > NOW() - INTERVAL '24 hours'
        ORDER BY created_at DESC LIMIT 20""")
    if not rows:
        record("recent_builds_24h", "WARNING", "No builds in last 24h", value=0)
        return
    files = []
    for row in rows:
        try:
            f = json.loads(row.get("payload", "{}")).get("file", "").split("/")[-1]
            if f: files.append(f)
        except Exception:
            pass
    record("recent_builds_24h", "OK",
           f"{len(rows)} builds: {', '.join(files[:5])}{'...' if len(files)>5 else ''}",
           value=len(rows))


# ── CHECK 5: Directive generator ───────────────────────────────────────────────────
def check_directive_generator():
    pids = pgrep("sentinel_directive_generator.py")
    if not pids:
        record("directive_generator", "WARNING", "not running")
    else:
        rows = ws_query("SELECT last_heartbeat FROM service_health WHERE service='sentinel_directive_generator'")
        if rows:
            age = age_mins(rows[0]["last_heartbeat"])
            record("directive_generator",
                   "WARNING" if age > DIRGEN_STALE_MINS else "OK",
                   f"PID {pids[0]}, heartbeat {age:.1f}m ago", value=round(age, 1))
        else:
            record("directive_generator", "OK", f"PID {pids[0]} running")

    pending = len([f for f in (SENTINEL / "directives").glob("*.json")
                   if ".done." not in f.name]) if (SENTINEL / "directives").exists() else 0
    record("directive_queue", "OK" if pending > 0 else "WARNING",
           f"{pending} directive(s) pending", value=pending)


# ── CHECK 6: Service health ────────────────────────────────────────────────────────
def check_service_health():
    rows     = ws_query("SELECT service, last_heartbeat FROM service_health ORDER BY service")
    api_ports = {8772: "write_service", 8773: "inference_router",
                 8780: "approval_workflow", 8781: "registry_api",
                 8790: "ui_server", 8795: "build_watcher_api"}
    down, degraded, healthy = [], [], []
    for row in rows:
        age = age_mins(row["last_heartbeat"])
        if   age < SVC_STALE_MINS: healthy.append(row["service"])
        elif age < SVC_DOWN_MINS:  degraded.append(row["service"])
        else:                       down.append(row["service"])
    status = "OK"
    parts  = [f"{len(healthy)} healthy"]
    if degraded: status = "WARNING"; parts.append(f"{len(degraded)} degraded: {', '.join(degraded)}")
    if down:     status = "WARNING"; parts.append(f"{len(down)} stale: {', '.join(down[:3])}")
    record("service_heartbeats", status, " | ".join(parts),
           value={"healthy": len(healthy), "degraded": len(degraded), "stale": len(down)})

    api_results = {name: http_check(f"http://127.0.0.1:{port}/health")
                   for port, name in api_ports.items()}
    failed = [f"{n}:{c}" for n, c in api_results.items() if c != 200]
    record("sentinel_apis",
           "CRITICAL" if api_results.get("write_service") != 200 else
           "WARNING"  if failed else "OK",
           f"Failed: {', '.join(failed)}" if failed else "All APIs responding",
           value=api_results)


# ── CHECK 7: Gate integrity (fixed -- reads service_health not mesh_events) ────
# Gates write to service_health as 'gate_orchestrator'.
# rc=1 = some gates failed = EXPECTED NORMAL STATE.
# rc=2 = infrastructure problem.
# The 290h false positive was from reading mesh_events which has no gate entries.
def check_gates():
    pids = pgrep("gate_scheduler.py")
    if not pids:
        record("gate_scheduler", "WARNING", "gate_scheduler.py not running")
    else:
        rows = ws_query("SELECT last_heartbeat FROM service_health WHERE service='gate_scheduler'")
        age  = age_mins(rows[0]["last_heartbeat"]) if rows else 9999
        record("gate_scheduler", "OK" if age < 30 else "WARNING",
               f"PID {pids[0]}, heartbeat {age:.1f}m ago", value=round(age, 1))

    rows = ws_query(
        "SELECT status, last_heartbeat, meta FROM service_health "
        "WHERE service='gate_orchestrator'"
    )
    if rows:
        row   = rows[0]
        age_h = age_mins(row["last_heartbeat"]) / 60
        try:   note = json.loads(row.get("meta") or "{}").get("note", "")
        except: note = ""
        is_infra_fail = "rc=2" in note
        record("gate_last_run",
               "CRITICAL" if is_infra_fail else
               "WARNING"  if age_h > 13 else "OK",  # >13h = missed 2 cycles
               f"Last run {age_h:.1f}h ago | {note} (rc=1 is normal)",
               value=round(age_h, 1))
    else:
        record("gate_last_run", "WARNING",
               "No gate_orchestrator entry in service_health")


# ── CHECK 8: Registry stats ──────────────────────────────────────────────────────
def check_registry():
    rows = ws_query("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN trust_score IS NOT NULL THEN 1 ELSE 0 END) as scored,
               ROUND(AVG(trust_score), 1) as avg_score,
               COUNT(DISTINCT verdict) as verdict_types
        FROM mcp_server_registry""")
    if not rows:
        record("registry_stats", "WARNING", "Could not query mcp_server_registry")
        return
    r = rows[0]
    total, scored = r.get("total", 0), r.get("scored", 0)
    pct = round((scored / max(total, 1)) * 100, 1)
    record("registry_stats", "OK",
           f"{total} MCPs, {scored} scored ({pct}%), avg={r.get('avg_score')}",
           value={"total": total, "scored": scored})
    vrows = ws_query("SELECT verdict, COUNT(*) as cnt FROM mcp_server_registry "
                     "GROUP BY verdict ORDER BY cnt DESC")
    dist = {row["verdict"]: row["cnt"] for row in vrows if row.get("verdict")}
    record("verdict_distribution", "OK",
           " | ".join(f"{k}: {v}" for k, v in dist.items()), value=dist)


# ── CHECK 9: DB table drift ───────────────────────────────────────────────────────
def check_db_drift():
    expected = {"mcp_server_registry", "mcp_signal_scores", "mcp_threat_associations",
                "mcp_attestations", "mcp_decisions", "mcp_submissions", "mcp_risk_register",
                "service_health", "mesh_events", "mesh_memory", "audit_log", "auth_tokens",
                "mcp_policy_rules", "mcp_fingerprints", "mcp_tool_hashes"}
    found   = {r["table_name"] for r in ws_query(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'")}
    missing = expected - found
    extra   = found - expected - {
        "corrections", "inference_log", "agent_runs", "agent_outputs",
        "write_queue_log", "perf_metrics", "world_articles", "world_topics",
        "github_velocity", "shodan_results", "npm_typosquat_alerts"
    }
    if missing:
        record("db_table_drift", "CRITICAL", f"Missing: {sorted(missing)}", value=list(missing))
    elif extra:
        record("db_table_drift", "WARNING",
               f"New tables not in schema doc: {sorted(extra)} -- run zm schema",
               value=list(extra))
    else:
        record("db_table_drift", "OK", f"{len(found)} tables present", value=len(found))


# ── CHECK 10: Error scan ───────────────────────────────────────────────────────────
def check_error_scan():
    error_logs = []
    cutoff_str = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
    for lf in LOGS.glob("*.log"):
        if lf.stat().st_size == 0: continue
        try:
            r = subprocess.run(["grep", "-i", "error", str(lf)],
                               capture_output=True, text=True, timeout=5)
            lines = [l for l in r.stdout.splitlines()
                     if l[:16] >= cutoff_str and "WARNING" not in l]
            if lines: error_logs.append(f"{lf.name}: {len(lines)} errors")
        except Exception:
            pass
    error_logs = [e for e in error_logs if "reuters" not in e.lower()]
    record("error_scan_24h",
           "WARNING" if error_logs else "OK",
           f"Error lines in: {', '.join(error_logs[:5])}" if error_logs
           else "No unexpected errors in last 24h",
           value=error_logs or None)


# ── CHECK 11: Temporal lineage ───────────────────────────────────────────────────
def check_temporal_lineage():
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()
    modified  = []

    for f in SENTINEL.rglob("*.py"):
        try:
            if f.stat().st_mtime > cutoff_ts:
                age_h = (datetime.now(timezone.utc).timestamp() - f.stat().st_mtime) / 3600
                modified.append((age_h, f.name))
        except Exception:
            pass
    for fname in ["KNOWLEDGE_BASE.md", "SENTINEL_DIRECTIVE_SCHEMA.md", "DB_SCHEMA.md",
                  "escalation.py", "BUILDER_BUILDER_SPEC.md", "SOC2_TICKET_INSPECTOR_SPEC.md"]:
        f = SENTINEL / fname
        try:
            if f.exists() and f.stat().st_mtime > cutoff_ts:
                age_h = (datetime.now(timezone.utc).timestamp() - f.stat().st_mtime) / 3600
                modified.append((age_h, fname))
        except Exception:
            pass
    for fname in ["go.sh", "zo_sentinel_builder.py", "zm_extra.zsh"]:
        f = MESH / fname
        try:
            if f.exists() and f.stat().st_mtime > cutoff_ts:
                age_h = (datetime.now(timezone.utc).timestamp() - f.stat().st_mtime) / 3600
                modified.append((age_h, f"zo_mesh/{fname}"))
        except Exception:
            pass

    modified.sort()
    names   = [f"{name} ({age_h:.1f}h ago)" for age_h, name in modified]
    newest  = f"{modified[-1][1]} ({modified[-1][0]:.1f}h ago)" if modified else "none"

    built_today = []
    manifest = SENTINEL / "BUILD_MANIFEST.md"
    if manifest.exists():
        cutoff_date = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d")
        for line in manifest.read_text().splitlines():
            if line.startswith("|") and cutoff_date in line and "OK" in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 5:
                    fname = parts[4].strip("`").strip()
                    if fname and fname not in built_today:
                        built_today.append(fname)

    parts = [f"{len(modified)} files modified"]
    if built_today: parts.append(f"{len(built_today)} built today")
    parts.append(f"newest: {newest}")
    record("temporal_lineage_24h", "OK" if modified or built_today else "WARNING",
           " | ".join(parts),
           value={"modified_files": names[:10], "built_today": built_today[:10],
                  "newest": newest})


# ── CHECK 12: New component health ───────────────────────────────────────────────
def check_new_component_health():
    cutoff_date = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d")
    manifest    = SENTINEL / "BUILD_MANIFEST.md"
    if not manifest.exists():
        record("new_component_health", "WARNING", "BUILD_MANIFEST.md not found")
        return

    newly_built = []
    for line in manifest.read_text().splitlines():
        if not line.startswith("|") or cutoff_date not in line or "OK" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 5:
            fname = parts[4].strip("`").strip()
            if fname.endswith(".py"): newly_built.append(fname)

    if not newly_built:
        record("new_component_health", "OK", "No new .py components built in last 24h")
        return

    not_running, running, skipped = [], [], []
    for fname in newly_built:
        fpath = SENTINEL / fname
        if not fpath.exists(): continue
        content = fpath.read_text(errors="ignore")
        is_daemon = ("uvicorn.run(" in content or
                     ("def run():" in content and "while True" in content) or
                     ("if __name__" in content and "run()" in content))
        if not is_daemon:
            skipped.append(fname)
            continue
        proc_name = fname.replace(".py", "")
        if pgrep(proc_name):
            running.append(fname)
        else:
            rows = ws_query(f"SELECT last_heartbeat FROM service_health WHERE service='{proc_name}'")
            if rows and age_mins(rows[0]["last_heartbeat"]) < 120:
                running.append(fname)
            else:
                not_running.append(fname)

    parts = []
    if running:     parts.append(f"{len(running)} running")
    if not_running: parts.append(f"{len(not_running)} BUILT NOT STARTED: {', '.join(not_running[:5])}")
    if skipped:     parts.append(f"{len(skipped)} utilities")
    record("new_component_health",
           "WARNING" if not_running else "OK",
           " | ".join(parts) or "No daemon components built recently",
           value={"running": running, "not_started": not_running})


# ── CHECK 13: Pending deployments ───────────────────────────────────────────────
def check_pending_deployments():
    pending = []

    # escalation.py built+tested but not imported by builder
    esc     = SENTINEL / "escalation.py"
    builder = MESH / "zo_sentinel_builder.py"
    if esc.exists() and builder.exists():
        if "escalation" not in builder.read_text(errors="ignore"):
            pending.append("escalation.py: tested but NOT wired into builder cascade")

    # Unreviewed patches
    patches_dir = SENTINEL / "builder_patches" / "pending"
    if patches_dir.exists():
        patches = list(patches_dir.glob("*.py")) + list(patches_dir.glob("*.json"))
        if patches:
            pending.append(f"builder_patches/pending/: {len(patches)} patch(es) awaiting review")

    # New API modules built but not in go.sh
    go_content = (MESH / "go.sh").read_text(errors="ignore") if (MESH / "go.sh").exists() else ""
    for fname in ["advanced_filter_api.py", "forensic_detail_api.py",
                  "manual_override_api.py", "email_guid_auth.py"]:
        if (SENTINEL / fname).exists() and fname not in go_content:
            pending.append(f"{fname}: built but not in go.sh")

    # SENTINEL_DIRECTIVE_SCHEMA updated after last dirgen cycle
    schema_path = SENTINEL / "SENTINEL_DIRECTIVE_SCHEMA.md"
    dirgen_rows = ws_query("SELECT last_heartbeat FROM service_health WHERE service='sentinel_directive_generator'")
    if schema_path.exists() and dirgen_rows:
        schema_age = (datetime.now(timezone.utc).timestamp() - schema_path.stat().st_mtime) / 60
        dirgen_age = age_mins(dirgen_rows[0]["last_heartbeat"])
        if schema_age < dirgen_age:
            pending.append(
                f"SENTINEL_DIRECTIVE_SCHEMA.md updated {schema_age:.0f}m ago but "
                f"directive generator last cycled {dirgen_age:.0f}m ago"
            )

    record("pending_deployments",
           "WARNING" if pending else "OK",
           f"{len(pending)} item(s): {pending[0][:80]}" if pending else "No pending deployments",
           value=pending or None)


# ── Report ────────────────────────────────────────────────────────────────────────
def print_report():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ok  = sum(1 for r in results if r["status"] == "OK")
    w   = sum(1 for r in results if r["status"] == "WARNING")
    c   = sum(1 for r in results if r["status"] == "CRITICAL")
    if JSON_MODE:
        print(json.dumps({"generated": now, "ok": ok, "warnings": w,
                          "critical": c, "checks": results}, indent=2))
        return
    W = 74
    print(f"\n{'='*W}")
    print(f"  ZOMesh Daily Check v2.1  {now}")
    print(f"{'='*W}")
    print(f"  OK: {ok}  WARNINGS: {w}  CRITICAL: {c}")
    print(f"{'-'*W}")
    icons = {"OK": "✅", "WARNING": "⚠️ ", "CRITICAL": "❌"}
    for r in results:
        icon   = icons.get(r["status"], "?")
        name   = r["check"].upper().replace("_", " ")[:26]
        detail = r["detail"][:W - 32]
        print(f"  {icon} {name:<26} {detail}")
        if r["check"] == "pending_deployments" and r["status"] == "WARNING":
            for item in (r.get("value") or []):
                print(f"       └ {item[:W-8]}")
        if r["check"] == "temporal_lineage_24h" and r.get("value"):
            v = r["value"]
            if v.get("built_today"):
                print(f"       Built:    {', '.join(v['built_today'][:5])}")
            if v.get("modified_files"):
                print(f"       Modified: {', '.join(v['modified_files'][:3])}")
        if r["check"] == "new_component_health" and r.get("value", {}).get("not_started"):
            for ns in r["value"]["not_started"][:5]:
                print(f"       └ NOT STARTED: {ns}")
    print(f"{'-'*W}")
    if c > 0:   print("  ACTION REQUIRED: run 'zm go'")
    elif w > 0: print("  Review warnings above")
    else:       print("  System healthy")
    print(f"{'='*W}\n")


def write_to_mesh(ok, w, c):
    ws_write("mesh_events", {
        "agent_id":   "daily_check",
        "event_type": "daily_health_report",
        "tier":       "T1",
        "payload":    json.dumps({"ok": ok, "warnings": w, "critical": c,
                                   "checks": [{"check": r["check"], "status": r["status"],
                                               "detail": r["detail"]} for r in results]}),
        "severity":   "INFO" if c == 0 and w == 0 else "WARNING" if c == 0 else "ERROR",
        "created_at": ts(),
    })


def maybe_fix():
    if not AUTO_FIX or critical_count == 0: return
    critical_checks = [r["check"] for r in results if r["status"] == "CRITICAL"]
    if "builder_process" in critical_checks or "sentinel_apis" in critical_checks:
        env = os.environ.copy()
        env_file = MESH / ".zo_env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("export "):
                    kv = line.replace("export ", "").split("=", 1)
                    if len(kv) == 2: env[kv[0]] = kv[1].strip('"')
        subprocess.run(["bash", str(MESH / "go.sh")], env=env)


if __name__ == "__main__":
    check_builder()
    check_minimax()
    check_last_build()
    check_recent_builds()
    check_directive_generator()
    check_service_health()
    check_gates()
    check_registry()
    check_db_drift()
    check_error_scan()
    check_temporal_lineage()
    check_new_component_health()
    check_pending_deployments()

    ok = sum(1 for r in results if r["status"] == "OK")
    w  = sum(1 for r in results if r["status"] == "WARNING")
    c  = sum(1 for r in results if r["status"] == "CRITICAL")

    print_report()
    write_to_mesh(ok, w, c)
    maybe_fix()

    sys.exit(0 if c == 0 and w == 0 else 1 if c == 0 else 2)