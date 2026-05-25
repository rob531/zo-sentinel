# ── CHECK 7: Gate integrity (FIXED 2026-04-23) ───────────────────────────────
# Gates write to service_health as 'gate_orchestrator', NOT to mesh_events.
# Previous version looked in mesh_events for 'gate%' events -- always showed
# 290h because that table has no gate entries. Fixed to read service_health.
# rc=1 from run_gates_periodic.py means 'some gates failed' -- EXPECTED,
# not an infrastructure error. rc=2 would be infra problem.

def check_gates():
    pids = pgrep("gate_scheduler.py")
    if not pids:
        record("gate_scheduler", "WARNING", "gate_scheduler.py not running")
    else:
        rows = ws_query("SELECT last_heartbeat FROM service_health WHERE service='gate_scheduler'")
        age  = age_mins(rows[0]["last_heartbeat"]) if rows else 9999
        record("gate_scheduler", "OK" if age < 30 else "WARNING",
               f"PID {pids[0]}, heartbeat {age:.1f}m ago", value=round(age, 1))

    # Read gate_orchestrator from service_health (where gates actually write)
    rows = ws_query(
        "SELECT status, last_heartbeat, meta FROM service_health "
        "WHERE service='gate_orchestrator'"
    )
    if rows:
        row   = rows[0]
        age_h = age_mins(row["last_heartbeat"]) / 60
        status_val = row.get("status", "?")
        try:
            meta = json.loads(row.get("meta") or "{}")
            note = meta.get("note", "")
        except Exception:
            note = ""
        # rc=1 is EXPECTED (some gates failed = normal state)
        # rc=2 would be infra problem
        is_infra_fail = "rc=2" in note
        record("gate_last_run",
               "CRITICAL" if is_infra_fail else
               "WARNING"  if age_h > 13 else "OK",   # >13h = missed 2 cycles
               f"Last run {age_h:.1f}h ago | {note}",
               value=round(age_h, 1))
    else:
        record("gate_last_run", "WARNING",
               "No gate_orchestrator heartbeat in service_health -- "
               "run_gates_periodic.py may never have run")