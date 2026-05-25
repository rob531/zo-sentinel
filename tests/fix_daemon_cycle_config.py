#!/usr/bin/env python3
"""
fix_daemon_cycle_config.py -- Correct expected_cycle_sec for services that
don't self-heartbeat (HTTP-driven services whose liveness is proven by their
/health endpoint, not by writing service_health rows).

Rationale:
    write_service and inference_router serve HTTP requests. Their liveness
    is proven by Gate 1's core_endpoint check. Self-heartbeats are optional
    for such services, so we set expected_cycle_sec=0 which makes Gate 1's
    heartbeat check skip them (the WHERE expected_cycle_sec > 0 filter).

    manager_agent stays at 30s because it runs a 30s polling loop and
    self-heartbeats as part of its cycle.

Idempotent. Safe to re-run.
"""
import duckdb
import sys
import time

DB = "/home/workspace/gate_errors.db"
RETRIES = 5
BACKOFF = 1.5

# Services that are HTTP-driven and don't need periodic self-heartbeat
HTTP_DRIVEN = ["write_service", "inference_router"]


def connect():
    for i in range(RETRIES):
        try:
            return duckdb.connect(DB)
        except duckdb.IOException as e:
            if "lock" in str(e).lower() and i < RETRIES - 1:
                time.sleep(BACKOFF * (i + 1))
                continue
            raise
    raise RuntimeError(f"could not acquire {DB} lock")


def main():
    con = connect()
    try:
        for svc in HTTP_DRIVEN:
            con.execute(
                "UPDATE daemon_cycle_config "
                "SET expected_cycle_sec = 0, heartbeat_grace_sec = 0, "
                "    notes = ? "
                "WHERE daemon_name = ?",
                [
                    "HTTP-driven, liveness via /health endpoint (Gate 1 core check). "
                    "No self-heartbeat expected.",
                    svc,
                ],
            )
        # Verify
        rows = con.execute(
            "SELECT daemon_name, expected_cycle_sec, heartbeat_grace_sec "
            "FROM daemon_cycle_config "
            "WHERE daemon_name IN ('write_service','inference_router') "
            "ORDER BY daemon_name"
        ).fetchall()
        for name, cyc, grace in rows:
            print(f"[OK] {name}: cycle={cyc}s grace={grace}s")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())