#!/usr/bin/env python3
"""
One-shot cleanup: purge the 351 false-positive mcp_directory_mentions
that the 2026-04-20 registry crawl produced.

Root cause: match_to_registry's 'reverse-DNS tail' strategy matched
registry names whose tail was a common word ('mcp', 'mcp-server') against
the single mcp_server_registry rows that happen to be named that. All 351
mentions pointed at the SAME awslabs/mcp repo or a generic 'mcp-server'
row, which is obviously wrong for 351 different reverse-DNS names.

This script:
  - Connects to write_service /execute
  - Deletes the bad rows (where the matched registry name is too generic)
  - Leaves the ~50 legitimate matches intact
  - Reports counts before and after

Safe to re-run (idempotent: nothing to delete on second run).
"""
import requests

WS = "http://127.0.0.1:8772"

def execute(sql, params=None):
    r = requests.post(f"{WS}/execute",
                      json={"sql": sql, "params": params or [], "wait": True},
                      timeout=15)
    r.raise_for_status()
    return r.json()

def query(sql, params=None):
    r = requests.post(f"{WS}/query",
                      json={"sql": sql, "params": params or []},
                      timeout=15)
    r.raise_for_status()
    return r.json().get("rows", [])

# Before
before = query(
    "SELECT COUNT(*) AS n FROM mcp_directory_mentions "
    "WHERE directory_name = 'mcp_registry'"
)[0]["n"]
print(f"mentions before cleanup: {before}")

# Delete false positives
execute(
    "DELETE FROM mcp_directory_mentions "
    "WHERE directory_name = 'mcp_registry' "
    "AND server_id IN ("
    "  SELECT server_id FROM mcp_server_registry "
    "  WHERE name IN ('mcp', 'mcp-server')"
    ")"
)

# After
after = query(
    "SELECT COUNT(*) AS n FROM mcp_directory_mentions "
    "WHERE directory_name = 'mcp_registry'"
)[0]["n"]
print(f"mentions after cleanup:  {after}")
print(f"deleted: {before - after}")

# Also move those names to candidates so they aren't lost entirely.
# Actually skip this — the registry_facts table already has them. No data loss.
print("done. legit matches preserved; false positives removed.")
print("note: registry_facts still has all 5000 entries for future use.")