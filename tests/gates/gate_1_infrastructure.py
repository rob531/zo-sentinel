#!/usr/bin/env python3
"""
gate_1_infrastructure.py -- Dynamic checks of the services the pipeline depends on.

Checks:
    1. write_service  (:8772)  -- /health reachable
    2. inference_router (:8771) -- /health reachable
    3. registry_api  (:8781) -- /health reachable
    4. ollama        (:11434) -- /api/tags responds with loaded models
    5. ui_server     (:8790)  -- /health reachable (2026-04-17: added after
                                 preview-down incident to catch silent UI outages)
    6. All sentinel daemons (with cycle>0) have heartbeats within expected_cycle + grace
    7. Critical tables exist (mcp_signal_enrichments, service_health, etc.)

Design notes (from this session's learnings):
    - Uses the framework's throttled HTTP helpers -- no bare requests.post bypasses
    - Each daemon's expected cycle comes from daemon_cycle_config seeded during
      bootstrap -- staying in one source of truth
    - Fast: all checks are single HTTP calls, no polling
    - Dedup-friendly: error signatures are stable across runs so repeat failures
      increment occurrence_count instead of spawning new novels
"""
import sys
import time
from typing import Optional

sys.path.insert(0, "/home/workspace/zo_sentinel/tests/gates")
from gate_framework import Gate, gate_run, ws_query, _throttle
import requests


CORE_ENDPOINTS = [
    ("write_service",    "http://127.0.0.1:8772/health",         "health"),
    ("inference_router", "http://127.0.0.1:8771/health",         "health"),
    ("registry_api",     "http://127.0.0.1:8781/health",         "health"),
    ("ollama",           "http://127.0.0.1:11434/api/tags",      "tags"),
    # ui_server serves the Zo preview UI. Failures here manifest as
    # "zite-8790-robinc.zo.computer refused to connect" in the browser.
    ("ui_server",        "http://127.0.0.1:8790/health",         "health"),
]

# Tables that must exist in main DuckDB for any of the pipeline to work.
# These are schema survival checks -- if ZoComputer wipes DuckDB on reboot
# and full_schema_bootstrap.py didn't re-run, this catches it.
CRITICAL_TABLES = [
    "mcp_server_registry",
    "mcp_signal_scores",
    "mcp_threat_associations",
    "mcp_risk_register",
    "mcp_attestations",
    "mcp_signal_enrichments",
    "service_health",
]


class Gate1Infrastructure(Gate):
    name = "gate_1_infrastructure"

    def run(self):
        print(f"\n-- {self.name} --")
        self._check_core_endpoints()
        self._check_daemon_heartbeats()
        self._check_critical_tables_exist()

    # ---- core service endpoints ----
    def _check_core_endpoints(self):
        for service, url, kind in CORE_ENDPOINTS:
            _throttle()
            # ui_server is "soft-critical": its absence doesn't break the
            # pipeline but does break the preview. We tag remediation
            # differently so noise is understandable.
            is_soft = (service == "ui_server")
            try:
                r = requests.get(url, timeout=5)
                reachable = (r.status_code == 200)
                detail = ""
                if reachable and kind == "tags":
                    try:
                        models = r.json().get("models", [])
                        if not models:
                            reachable = False
                            detail = "no models loaded"
                        else:
                            detail = f"{len(models)} model(s) available"
                    except Exception as e:
                        reachable = False
                        detail = f"bad json: {e}"
                self.check(
                    f"{service} ({url}) reachable",
                    condition=reachable,
                    error_class="infra_unreachable",
                    expected="HTTP 200",
                    actual=f"HTTP {r.status_code} {detail}".strip(),
                    remediation=(
                        "Start ui_server: nohup python3 "
                        "/home/workspace/zo_sentinel/ui_server.py >> "
                        "/home/workspace/logs/sentinel_ui_server.log 2>&1 & "
                        "-- or add to start_sentinel_pipeline.sh SERVICES"
                        if is_soft else
                        f"Restart {service}; check logs at "
                        f"/home/workspace/logs/{service}.log"
                    ),
                )
            except requests.RequestException as e:
                self.check(
                    f"{service} ({url}) reachable",
                    condition=False,
                    error_class="infra_unreachable",
                    expected="connection succeeds",
                    actual=f"{type(e).__name__}: {e}",
                    remediation=(
                        "ui_server not running. Preview at zite-8790-robinc.zo.computer "
                        "will 'refused to connect'. Run install patch and start manually."
                        if is_soft else
                        f"Service {service} appears down; run 'zm go'"
                    ),
                )

    # ---- daemon heartbeat freshness ----
    def _check_daemon_heartbeats(self):
        """For each daemon in daemon_cycle_config with a non-zero expected cycle,
        confirm its most recent heartbeat is within expected + grace seconds.

        Services with expected_cycle_sec=0 (HTTP-driven services like
        write_service and inference_router, or FastAPI apps like registry_api
        and ui_server) are proven alive by the core_endpoint check above and
        don't need a self-heartbeat."""
        cfg_rows = self.db.con.execute(
            "SELECT daemon_name, expected_cycle_sec, heartbeat_grace_sec "
            "FROM daemon_cycle_config "
            "WHERE expected_cycle_sec > 0"
        ).fetchall()

        for daemon, expected, grace in cfg_rows:
            try:
                rows = ws_query(
                    "SELECT CAST(EXTRACT(EPOCH FROM (now() - last_heartbeat)) "
                    "AS INTEGER) AS age FROM service_health WHERE service = ?",
                    params=[daemon],
                )
            except Exception as e:
                self.check(
                    f"heartbeat query for {daemon}",
                    condition=False,
                    error_class="infra_unreachable",
                    actual=str(e)[:100],
                )
                continue

            if not rows:
                self.check(
                    f"{daemon}: heartbeat row exists",
                    condition=False,
                    error_class="heartbeat_missing",
                    expected=f"row in service_health for '{daemon}'",
                    actual="no row found",
                    remediation=(
                        f"Daemon '{daemon}' is not writing service_health. "
                        "Check its source for send_heartbeat() call and ensure "
                        "it's invoked in the main loop."
                    ),
                )
                continue

            age = rows[0].get("age")
            threshold = expected + grace
            ok = (age is not None and age <= threshold)
            self.check(
                f"{daemon} heartbeat within {threshold}s",
                condition=ok,
                error_class="heartbeat_stale",
                expected=f"age <= {threshold}s (cycle={expected}s + grace={grace}s)",
                actual=f"age={age}s",
                remediation=(
                    f"Daemon '{daemon}' cycle is stalled. Check "
                    f"/home/workspace/logs/sentinel_{daemon}.log for errors, "
                    "or run 'bash start_sentinel_pipeline.sh' to restart the suite."
                ),
            )

    # ---- critical tables exist ----
    def _check_critical_tables_exist(self):
        """Uses per-table query (Gate 2 learning) to avoid info_schema truncation."""
        for table in CRITICAL_TABLES:
            try:
                rows = ws_query(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'main' AND table_name = ?",
                    params=[table],
                )
                col_count = len(rows)
            except Exception as e:
                self.check(
                    f"table '{table}' check",
                    condition=False,
                    error_class="infra_unreachable",
                    actual=str(e)[:100],
                )
                continue

            self.check(
                f"table '{table}' exists with columns",
                condition=(col_count > 0),
                error_class="stale_schema_ref",
                expected="table present with >=1 column",
                actual=f"{col_count} columns",
                remediation=(
                    f"Re-run /home/workspace/zo_sentinel/full_schema_bootstrap.py "
                    f"(or enrichment_schema_bootstrap.py for mcp_signal_enrichments); "
                    "then ensure the bootstrap is in 'zm go' so it re-runs post-reboot."
                ),
            )


def main() -> int:
    with gate_run(trigger="manual", host_state="steady-state") as (db, run_id):
        gate = Gate1Infrastructure(db, run_id)
        gate.run()
        print(f"\nGate 1: {gate.checks - gate.failures}/{gate.checks} checks passed")
        return 1 if gate.failures else 0


if __name__ == "__main__":
    sys.exit(main())