#!/usr/bin/env python3
"""
watch.py -- ZO-SENTINEL live dashboard.
Fixed 2026-04-22:
  - SQL queries corrected to match live DB schema
  - Daemon list updated to persistent processes only (removed one-shot sentinel scripts)
  - Added registry_api and approval_workflow to daemon list
Run: python3 watch.py
     python3 watch.py --interval 10
"""
import sys, os, time, argparse, subprocess, requests
from typing import Dict, List, Any, Optional

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL     = f"{WRITE_SERVICE}/query"

ANSI_CLEAR = "\033[2J\033[H"
ANSI_HOME  = "\033[H"
ANSI_RESET = "\033[0m"
RESET      = ANSI_RESET
BOLD  = "\033[1m"
DIM   = "\033[2m"
RED     = "\033[91m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
BLUE    = "\033[94m"
MAGENTA = "\033[95m"
CYAN    = "\033[96m"
WHITE   = "\033[97m"

# Only persistent processes that are meant to run continuously.
# One-shot sentinel scripts (signal_analyser, rug_pull_monitor, etc.)
# are NOT included here -- they run, do work, and exit.
DAEMONS = [
    # ZOMesh core
    ("write_service",        "write_service.py"),
    ("inference_router",     "inference_router_service.py"),
    ("pipeline_bridge",      "pipeline_bridge.py"),
    ("manager_agent",        "run_manager.py"),
    ("t2_consumer",          "t2_consumer_agents.py"),
    ("liveness_probe",       "liveness_probe.py"),
    ("signal_bridge",        "signal_bridge.py"),
    ("gate_scheduler",       "gate_scheduler.py"),
    # Sentinel persistent
    ("zo_sentinel_builder",  "zo_sentinel_builder.py"),
    ("directive_generator",  "sentinel_directive_generator.py"),
    ("registry_api",         "registry_api.py"),
    ("approval_workflow",    "approval_workflow.py"),
    ("ui_server",            "ui_server.py"),
    ("build_watcher_api",    "build_watcher_api.py"),
    ("ecosystems_fetcher",   "ecosystems_metadata_fetcher.py"),
    # World layer
    ("world_agent",          "run.py --daemon"),
    ("world_article_feeder", "world_article_feeder.py"),
    ("intent_engine",        "intent_engine_daemon.py"),
]


def ws_query(sql: str) -> List[Dict]:
    try:
        r = requests.post(QUERY_URL, json={"sql": sql}, timeout=10)
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception:
        pass
    return []


def get_daemon_status() -> List[Dict[str, Any]]:
    result = []
    for name, proc_match in DAEMONS:
        try:
            r = subprocess.run(["pgrep", "-f", proc_match],
                               capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                pid = r.stdout.strip().split("\n")[0]
                uptime = subprocess.run(
                    ["ps", "-o", "etime=", "-p", pid],
                    capture_output=True, text=True
                ).stdout.strip()
                result.append({"name": name, "pid": pid,
                               "status": "RUNNING", "uptime": uptime or "-"})
            else:
                result.append({"name": name, "pid": None,
                               "status": "STOPPED", "uptime": None})
        except Exception:
            result.append({"name": name, "pid": None,
                           "status": "UNKNOWN", "uptime": None})
    return result


def get_mesh_events(limit: int = 6) -> List[Dict]:
    # mesh_events: agent_id, event_type, tier, payload, severity, created_at
    return ws_query(f"""
        SELECT agent_id, event_type, severity, created_at
        FROM mesh_events
        ORDER BY created_at DESC
        LIMIT {limit}
    """)


def get_pipeline_health() -> Dict[str, int]:
    # service_health: service, status, last_heartbeat, meta
    rows = ws_query("""
        SELECT service, last_heartbeat
        FROM service_health
        ORDER BY service
    """)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    counts = {"healthy": 0, "degraded": 0, "down": 0, "unknown": 0}
    for row in rows:
        hb = row.get("last_heartbeat")
        if hb:
            try:
                ts  = datetime.fromisoformat(str(hb).replace("Z", "+00:00"))
                age = (now - ts).total_seconds() / 60
                if   age < 10:  counts["healthy"]  += 1
                elif age < 30:  counts["degraded"] += 1
                elif age < 120: counts["down"]      += 1
                else:           counts["unknown"]   += 1
            except Exception:
                counts["unknown"] += 1
        else:
            counts["unknown"] += 1
    return counts


def get_recent_threats(limit: int = 3) -> List[Dict]:
    return ws_query(f"""
        SELECT server_id, threat_type, severity, reported_at
        FROM mcp_threat_associations
        ORDER BY reported_at DESC
        LIMIT {limit}
    """)


def get_assessment_queue_depth() -> int:
    rows = ws_query("""
        SELECT COUNT(*) as cnt
        FROM mcp_server_registry
        WHERE trust_score IS NULL
    """)
    return rows[0].get("cnt", 0) if rows else 0


def get_registry_summary() -> Dict:
    rows = ws_query("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN verdict LIKE 'TRUSTED%' THEN 1 ELSE 0 END) as trusted,
            SUM(CASE WHEN verdict LIKE 'CAUTION%' OR verdict LIKE 'HIGH_RISK%' THEN 1 ELSE 0 END) as risky,
            SUM(CASE WHEN trust_score IS NULL THEN 1 ELSE 0 END) as unscored,
            ROUND(AVG(trust_score), 1) as avg_score
        FROM mcp_server_registry
    """)
    return rows[0] if rows else {"total": 0, "trusted": 0, "risky": 0, "unscored": 0, "avg_score": 0}


def draw_header():
    print(f"{ANSI_HOME}{BOLD}{CYAN}{'='*80}{ANSI_RESET}")
    print(f"{BOLD}{CYAN}  ZO-SENTINEL{RESET}  |  {WHITE}{time.strftime('%Y-%m-%d %H:%M:%S')}{RESET}  |  "
          f"{DIM}Ctrl+C to exit{RESET}")
    print(f"{BOLD}{CYAN}{'='*80}{ANSI_RESET}\n")


def draw_daemons(daemons: List[Dict]):
    running = sum(1 for d in daemons if d["status"] == "RUNNING")
    total   = len(daemons)
    print(f"{BOLD}{MAGENTA}\u250c\u2500 PERSISTENT DAEMONS  {running}/{total} running {'\u2500'*48}\u2510{ANSI_RESET}")
    for d in daemons:
        pid_str = f"PID:{d['pid']}" if d["pid"] else "---"
        uptime  = d["uptime"] or "-"
        col     = GREEN if d["status"] == "RUNNING" else RED if d["status"] == "STOPPED" else YELLOW
        icon    = "\u25cf" if d["status"] == "RUNNING" else "\u25cb"
        print(f"{MAGENTA}\u2502{ANSI_RESET} {col}{icon}{RESET} "
              f"{BOLD}{d['name']:<28}{RESET}  "
              f"{DIM}{pid_str:<12}{RESET}  "
              f"{CYAN}{uptime}{RESET}")
    print(f"{MAGENTA}\u2514{'\u2500'*76}\u2518{ANSI_RESET}\n")


def draw_events(events: List[Dict]):
    print(f"{BOLD}{BLUE}\u250c\u2500 RECENT MESH EVENTS {'\u2500'*60}\u2510{ANSI_RESET}")
    if not events:
        print(f"{BLUE}\u2502{ANSI_RESET}  {DIM}No events{ANSI_RESET}")
    for e in events:
        agent  = (e.get("agent_id") or "?")[:22]
        evtype = (e.get("event_type") or "?")[:22]
        sev    = (e.get("severity") or "INFO")[:8]
        ts     = str(e.get("created_at") or "")[:16]
        col = RED if "error" in evtype.lower() else YELLOW if "threat" in evtype.lower() else \
              GREEN if "build_complete" in evtype else CYAN
        print(f"{BLUE}\u2502{ANSI_RESET} {col}{evtype:<22}{RESET}  "
              f"{DIM}{agent:<22}{RESET}  {DIM}{sev:<8}{RESET}  {DIM}{ts}{RESET}")
    print(f"{BLUE}\u2514{'\u2500'*76}\u2518{ANSI_RESET}\n")


def draw_health(counts: Dict):
    total = sum(counts.values())
    print(f"{BOLD}{GREEN}\u250c\u2500 SERVICE HEARTBEATS  ({total} services) {'\u2500'*44}\u2510{ANSI_RESET}")
    for label, col, key in [
        ("HEALTHY  (<10m)",  GREEN,  "healthy"),
        ("DEGRADED (<30m)",  YELLOW, "degraded"),
        ("DOWN     (<2h)",   RED,    "down"),
        ("STALE    (>2h)",   DIM,    "unknown"),
    ]:
        n   = counts.get(key, 0)
        bar = "\u2588" * min(n * 2, 30)
        print(f"{col}\u2502{ANSI_RESET} {col}{label:<16}{RESET}  "
              f"{col}{bar:<30}{RESET}  {BOLD}{col}{n}{RESET}")
    print(f"{GREEN}\u2514{'\u2500'*76}\u2518{ANSI_RESET}\n")


def draw_threats(threats: List[Dict]):
    print(f"{BOLD}{RED}\u250c\u2500 RECENT THREATS {'\u2500'*64}\u2510{ANSI_RESET}")
    if not threats:
        print(f"{RED}\u2502{ANSI_RESET}  {DIM}No threats{ANSI_RESET}")
    for t in threats:
        sev  = (t.get("severity") or "?")[:8]
        sid  = (t.get("server_id") or "?")[:25]
        tt   = (t.get("threat_type") or "?")[:20]
        ts   = str(t.get("reported_at") or "")[:16]
        col  = RED if sev in ("CRITICAL", "HIGH") else YELLOW
        print(f"{RED}\u2502{ANSI_RESET} {col}{sev:<8}{RESET}  "
              f"{YELLOW}{sid:<25}{RESET}  {RED}{tt:<20}{RESET}  {DIM}{ts}{RESET}")
    print(f"{RED}\u2514{'\u2500'*76}\u2518{ANSI_RESET}\n")


def draw_registry(depth: int, summary: Dict):
    total = summary.get("total", 0)
    print(f"{BOLD}{YELLOW}\u250c\u2500 REGISTRY  {total} MCPs  avg_score={summary.get('avg_score',0)} {'\u2500'*50}\u2510{ANSI_RESET}")
    print(f"{YELLOW}\u2502{ANSI_RESET}  "
          f"Trusted: {GREEN}{summary.get('trusted',0)}{RESET}   "
          f"Risky: {RED}{summary.get('risky',0)}{RESET}   "
          f"Unscored: {CYAN}{depth}{RESET}")
    if total > 0:
        bar_len = min(int((summary.get('trusted', 0) / total) * 60), 60)
        bar = f"{GREEN}{'\u2588' * bar_len}{DIM}{'\u2591' * (60 - bar_len)}{RESET}"
        print(f"{YELLOW}\u2502{ANSI_RESET}  Trust coverage: [{bar}]")
    print(f"{YELLOW}\u2514{'\u2500'*76}\u2518{ANSI_RESET}\n")


def draw_footer():
    print(f"{DIM}{'\u2500'*80}{ANSI_RESET}")
    print(f"{DIM}zm watch  |  zm watch10  |  zm test  |  zm apis  |  refresh={INTERVAL}s{ANSI_RESET}")


INTERVAL = 30


def run():
    global INTERVAL
    parser = argparse.ArgumentParser(description="ZO-SENTINEL Watch Mode")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    INTERVAL = args.interval

    print(ANSI_CLEAR)
    print(f"{BOLD}{CYAN}ZO-SENTINEL Watch Mode{RESET}")
    print(f"{DIM}{len(DAEMONS)} persistent daemons monitored | refresh {INTERVAL}s{ANSI_RESET}\n")
    time.sleep(1)

    try:
        while True:
            print(ANSI_CLEAR)
            daemons = get_daemon_status()
            events  = get_mesh_events(6)
            health  = get_pipeline_health()
            threats = get_recent_threats(3)
            depth   = get_assessment_queue_depth()
            summary = get_registry_summary()
            draw_header()
            draw_daemons(daemons)
            draw_events(events)
            draw_health(health)
            draw_threats(threats)
            draw_registry(depth, summary)
            draw_footer()
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print(f"\n{GREEN}Shutting down.{RESET}\n")
        sys.exit(0)


if __name__ == "__main__":
    run()