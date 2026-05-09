"""
zo-sentinel CI smoke audit
==========================

Boots ui_server.py on 127.0.0.1:8790, waits for /healthz, then probes every
declared route. For each route we assert the response is non-5xx and snapshot
the first 200 bytes of the body so a reviewer can eyeball drift without the
full HTML in the log.

Designed for the GitHub Actions runner (no browser, no DB). Routes that
depend on the write_service degrade gracefully via the catch-all middleware
in ui_server.py — they return JSON {"rows":[],"error":...} or empty data,
which is still a 200 from this script's perspective.

Exit code:
  0 = every route returned non-5xx
  1 = one or more routes returned 5xx, threw, or never came up
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List

import requests

BASE = os.environ.get("UI_BASE", "http://127.0.0.1:8790")
TIMEOUT = float(os.environ.get("REQ_TIMEOUT", "5"))

# (method, path, body)
ROUTES: List[Dict[str, Any]] = [
    {"method": "GET",  "path": "/"},
    {"method": "GET",  "path": "/healthz"},
    {"method": "GET",  "path": "/api/recent?limit=5"},
    {"method": "GET",  "path": "/api/search?q=demo"},
    {"method": "GET",  "path": "/api/registry?limit=5"},
    {"method": "GET",  "path": "/api/audit?limit=5"},
    {"method": "GET",  "path": "/api/admin/threats?limit=5"},
    {"method": "GET",  "path": "/api/admin/risk?limit=5"},
    {"method": "GET",  "path": "/admin-threats"},
    {"method": "GET",  "path": "/admin-risk"},
    {"method": "GET",  "path": "/submit"},
    {"method": "GET",  "path": "/mcp/example-server"},
    {
        "method": "POST",
        "path": "/api/submit",
        "body": {
            "mcp_identifier": "demo-mcp",
            "requester_name": "CI Smoke",
            "requester_team": "ci",
            "business_purpose": "smoke-test invocation, ignore",
            "environment": "Development",
        },
    },
    {
        # Should produce a clean 422 from Pydantic validation, not a 500.
        "method": "POST",
        "path": "/api/submit",
        "body": {"mcp_identifier": ""},
        "expect_status": 422,
    },
]


def wait_for_healthz(deadline_seconds: float = 30.0) -> None:
    start = time.monotonic()
    last_err: str | None = None
    while time.monotonic() - start < deadline_seconds:
        try:
            r = requests.get(f"{BASE}/healthz", timeout=2)
            if r.status_code == 200:
                return
            last_err = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
        time.sleep(0.5)
    raise RuntimeError(f"ui_server never became healthy at {BASE}/healthz: {last_err}")


def probe(route: Dict[str, Any]) -> Dict[str, Any]:
    method = route["method"]
    path = route["path"]
    expected = route.get("expect_status")
    try:
        if method == "GET":
            r = requests.get(BASE + path, timeout=TIMEOUT)
        else:
            r = requests.post(BASE + path, json=route.get("body"), timeout=TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return {"path": path, "method": method, "ok": False, "error": str(e)}

    snippet = r.content[:200].decode("utf-8", errors="replace")
    if expected is not None:
        ok = r.status_code == expected
    else:
        ok = r.status_code < 500
    return {
        "path": path,
        "method": method,
        "status": r.status_code,
        "ok": ok,
        "snippet": snippet,
    }


def main() -> int:
    print(f"smoke_audit: waiting for {BASE}/healthz …")
    wait_for_healthz()
    print("smoke_audit: healthz ok, probing routes …\n")

    results = [probe(r) for r in ROUTES]
    failed = [r for r in results if not r.get("ok")]

    for r in results:
        marker = "OK " if r.get("ok") else "FAIL"
        status = r.get("status", "ERR")
        snippet = (r.get("snippet") or r.get("error") or "").replace("\n", " ")[:160]
        print(f"  [{marker}] {r['method']:4s} {r['path']:32s} → {status}  {snippet}")

    print()
    summary = {
        "total": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "failures": failed,
    }
    out_dir = os.environ.get("SMOKE_OUT", ".")
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, "smoke_results.json")
    with open(out_file, "w") as f:
        json.dump({"summary": summary, "results": results}, f, indent=2)
    print(f"smoke_audit: wrote {out_file}")

    if failed:
        print(f"smoke_audit: {len(failed)} route(s) failed")
        return 1
    print(f"smoke_audit: all {len(results)} routes returned non-5xx")
    return 0


if __name__ == "__main__":
    sys.exit(main())
