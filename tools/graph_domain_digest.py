#!/usr/bin/env python3
"""graph_domain_digest.py -- print a COMPACT domain map from the graphify code graph
(Leiden communities = the app's domains) + the parked list, for seeding goose Memory
so the Directive Architect recalls the app landscape and RANGES across it instead of
fixating on a few subjects. Stdlib only; queries the :8772 bus.

    python3 tools/graph_domain_digest.py
"""
import json, sys, urllib.request

BUS = "http://127.0.0.1:8772/query"
PARKED = ("snow_connector, aidr_commit_gateway, approval_evidence_bundler, and the "
          "SNOW + AIDR external-client authorization work")
SQL = ("SELECT community, COUNT(*) AS modules, "
       "MIN(regexp_replace(source_file, '^.*/', '')) AS example "
       "FROM code_nodes WHERE source_file LIKE '%.py' "
       "AND source_file NOT LIKE 'directives/%' AND source_file NOT LIKE 'directives_archive/%' "
       "GROUP BY community ORDER BY modules DESC LIMIT 30")


def format_digest(rows) -> str:
    """Compact, budget-lean domain map (loaded into every architect prompt). PURE."""
    lines = ["DOMAIN MAP (graphify code-graph communities = the app's domains; "
             "RANGE across these, do NOT fixate on a few subjects):"]
    for r in rows or []:
        lines.append(f"- domain {r.get('community')}: {r.get('modules')} modules "
                     f"(e.g. {r.get('example') or '?'})")
    lines.append("")
    lines.append("PARKED (do NOT propose build / verify / investigate for): " + PARKED + ".")
    return "\n".join(lines)


def fetch(sql, timeout=10):
    req = urllib.request.Request(BUS, data=json.dumps({"sql": sql}).encode(),
                                 headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return (json.loads(r.read().decode("utf-8", "replace")) or {}).get("rows", [])


def main():
    try:
        rows = fetch(SQL)
    except Exception as e:
        print(f"[error] could not reach bus at {BUS}: {e}", file=sys.stderr)
        return 1
    print(format_digest(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
