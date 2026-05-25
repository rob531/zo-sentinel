#!/usr/bin/env python3
"""
Probe: does the detail endpoint GET /v0/servers/{name} return packages
when the list endpoint does not?

Also probes the edge case: does fetching a well-known server like
io.modelcontextprotocol/filesystem return its full package identifier?
"""
import json
import urllib.parse
import requests

BASE = "https://registry.modelcontextprotocol.io"
H = {
    "User-Agent": "zo-sentinel/1.0 (mcp-trust-intelligence)",
    "From": "hello@zocomputer.io",
    "Accept": "application/json",
}

# 1. Get the list, pull a handful of names
r = requests.get(BASE + "/v0/servers", params={"limit": 5, "version": "latest"}, headers=H, timeout=20)
r.raise_for_status()
data = r.json()
names = [(s.get("server") or {}).get("name") for s in (data.get("servers") or [])]
names = [n for n in names if n]
print(f"=== Got {len(names)} names from list ===")
for n in names:
    print(f"  - {n}")

# 2. Search for a known server we expect packages on
print("\n=== Search test: 'filesystem' ===")
r2 = requests.get(BASE + "/v0/servers", params={"search": "filesystem", "limit": 5, "version": "latest"}, headers=H, timeout=20)
r2.raise_for_status()
d2 = r2.json()
fs_names = [(s.get("server") or {}).get("name") for s in (d2.get("servers") or [])]
fs_names = [n for n in fs_names if n]
print(f"found {len(fs_names)} filesystem-matching names:")
for n in fs_names:
    print(f"  - {n}")

# 3. Fetch detail for the first name (from generic list) and a filesystem name (if any)
probe_names = names[:2] + fs_names[:2]
for name in probe_names:
    print(f"\n=== detail for {name} ===")
    encoded = urllib.parse.quote(name, safe="")
    try:
        rd = requests.get(f"{BASE}/v0/servers/{encoded}", headers=H, timeout=20)
        print(f"status: {rd.status_code}")
        if rd.status_code == 200:
            body = rd.json()
            # Print just the interesting subset so output is readable
            pkgs = body.get("packages") or []
            print(f"packages count: {len(pkgs)}")
            if pkgs:
                print("first package:")
                print(json.dumps(pkgs[0], indent=2)[:800])
            server = body.get("server") or {}
            print(f"server.name: {server.get('name')}")
            print(f"server.description: {(server.get('description') or '')[:80]}")
            # Also check if body itself has description/name (some APIs flatten)
            if not server:
                print("NOTE: no 'server' key at top level")
                print("top-level keys:", list(body.keys()))
        else:
            print(f"body: {rd.text[:300]}")
    except Exception as e:
        print(f"error: {e}")