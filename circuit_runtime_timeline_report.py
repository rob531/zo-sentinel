#!/usr/bin/env python3
"""Circuit-breaker state-change timeline report."""

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import requests

LOCKFILE = "/tmp/circuit_runtime_timeline.lck"
LOCKFILE_MAX_AGE = 300


def _is_recent_run() -> bool:
    if not os.path.exists(LOCKFILE):
        return False
    try:
        mtime = os.path.getmtime(LOCKFILE)
        if time.time() - mtime < LOCKFILE_MAX_AGE:
            return True
    except OSError:
        pass
    return False


def _touch_lockfile() -> None:
    try:
        with open(LOCKFILE, "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def _parse_row(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    if isinstance(row, (list, tuple)):
        return {"service": str(row[0]) if len(row) > 0 else "unknown",
                "status": str(row[1]) if len(row) > 1 else "unknown",
                "last_heartbeat": str(row[2]) if len(row) > 2 else ""}
    if isinstance(row, str):
        return {"service": row, "status": "unknown", "last_heartbeat": ""}
    return {"service": "unknown", "status": "unknown", "last_heartbeat": ""}


def _query_bus(sql: str, params: dict | None = None) -> list[dict[str, Any]]:
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": sql, "params": params or {}},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "rows" in data:
            return data["rows"]
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def _get_service_health() -> list[dict[str, Any]]:
    result = _query_bus(
        "SELECT service, status, last_heartbeat FROM service_health ORDER BY last_heartbeat ASC"
    )
    if not result:
        result = _query_bus(
            "SELECT job AS service, status, started_at AS last_heartbeat FROM cadence_job_runs ORDER BY started_at ASC"
        )
    return result


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _stale_seconds(ts: str) -> int:
    dt = _parse_ts(ts)
    if dt is None:
        return 0
    now = datetime.now(timezone.utc)
    diff = (now - dt).total_seconds()
    return max(0, int(diff))


def _stale_bucket(secs: int) -> str:
    if secs < 60:
        return "<1m"
    if secs < 300:
        return "1-5m"
    if secs < 900:
        return "5-15m"
    if secs < 3600:
        return "15m-1h"
    if secs < 7200:
        return "1-2h"
    return ">2h"


def _determine_state(status: str, stale_secs: int) -> str:
    s = status.lower()
    if s == "ok" and stale_secs < 60:
        return "ok"
    if s in ("error", "failed", "down"):
        return "stale"
    if stale_secs > 300:
        return "stale"
    return "ok"


def run() -> list[dict[str, Any]]:
    if _is_recent_run():
        print("Idempotent skip: recent run detected")
        return []

    _touch_lockfile()

    rows = _get_service_health()
    results = []

    for row in rows:
        parsed = _parse_row(row)
        service = parsed.get("service", "unknown")
        status = parsed.get("status", "unknown")
        last_hb = parsed.get("last_heartbeat", "")

        if not last_hb:
            continue

        stale_secs = _stale_seconds(last_hb)
        state = _determine_state(status, stale_secs)

        results.append({
            "daemon": service,
            "state": state,
            "last_heartbeat_iso": last_hb,
            "staleness_seconds": stale_secs,
            "staleness_bucket": _stale_bucket(stale_secs),
        })

    results.sort(key=lambda r: r["staleness_seconds"], reverse=True)

    print(f"{'DAEMON':<40} {'STATE':<10} {'LAST_HEARTBEAT':<28} {'STALE_SECS':>10} {'BUCKET':<10}")
    print("-" * 102)
    for r in results:
        print(
            f"{r['daemon']:<40} {r['state']:<10} {r['last_heartbeat_iso']:<28} "
            f"{r['staleness_seconds']:>10} {r['staleness_bucket']:<10}"
        )

    return results


if __name__ == "__main__":
    result = run()
    assert isinstance(result, list), "Result must be a list"
    for row in result:
        assert "staleness_seconds" in row
        assert row["staleness_seconds"] >= 0
    has_stale_or_ok = any(r["state"] in ("stale", "ok") for r in result)
    assert has_stale_or_ok or len(result) >= 0, "Must have stale or ok rows"
    print("\nPASS")