#!/usr/bin/env python3
"""Gate-2 PROD smoke: assert the LIVE verdict API returns real values for all 7
named axes for a set of known server_ids, fail-closed on null/synthetic. Ops tool
(not CI) — needs a bearer token and known IDs.

Usage:
  MCPLOOKUP_BASE=https://mcplookup.fly.dev \
  MCPLOOKUP_TOKEN=<jwt> \
  MCPLOOKUP_IDS=srvA,srvB \
  python axis_reality_smoke.py
"""
import os
import sys

import requests

AXES = {"overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface"}
BAD = {None, "", "STUB", "SYNTHETIC", "UNKNOWN_STUB"}


def main() -> int:
    base = os.environ.get("MCPLOOKUP_BASE", "https://mcplookup.fly.dev").rstrip("/")
    token = os.environ.get("MCPLOOKUP_TOKEN", "")
    ids = [s for s in os.environ.get("MCPLOOKUP_IDS", "").split(",") if s.strip()]
    if not token or not ids:
        print("FAIL: set MCPLOOKUP_TOKEN and MCPLOOKUP_IDS")
        return 2
    headers = {"Authorization": f"Bearer {token}"}
    bad = 0
    for sid in ids:
        try:
            r = requests.get(f"{base}/api/verdict/{sid}", headers=headers, timeout=20)
        except Exception as e:
            print(f"FAIL {sid}: request error {e}")
            bad += 1
            continue
        if r.status_code != 200:
            print(f"FAIL {sid}: HTTP {r.status_code}")
            bad += 1
            continue
        axes = (r.json() or {}).get("axes", {})
        missing = AXES - set(axes)
        nulls = [a for a in AXES if a in axes and axes[a].get("label") in BAD]
        if missing or nulls:
            print(f"FAIL {sid}: missing={sorted(missing)} null/synthetic={nulls}")
            bad += 1
        else:
            print(f"OK   {sid}: all 7 axes real")
    print(f"\n{'PASS' if bad == 0 else 'FAIL'}: {len(ids)-bad}/{len(ids)} servers returned real 7-axis verdicts")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
