#!/usr/bin/env python3
"""graph_domain_digest.py -- print a COMPACT domain map from the graphify code graph
(Leiden communities = the app's domains) + the parked list, for seeding goose Memory
so the Directive Architect recalls the app landscape and RANGES across it instead of
fixating on a few subjects. Stdlib only; queries the :8772 bus.

    python3 tools/graph_domain_digest.py
    python3 tools/graph_domain_digest.py --self-test

WHY THE QUERY CHANGED (2026-09-02, graphify-kl-daily-refresh, FU-110)
---------------------------------------------------------------------
This tool was DARK -- the 2026-09-02 census found 0 repo callers, 0 lane prompts,
0 agent docs -- so nothing had ever read its output. Run live against the bus it
turned out to be wrong in two ways at once, both of which land in the architect
prompt as confident text:

  1. THE EXEMPLAR COLUMN COULD NOT NAME A REAL MODULE. `MIN(basename)` is
     alphabetical, and `_` (0x5F) sorts before every lowercase letter, so any
     community containing an `__init__.py` reported `__init__.py` as its example.
     Measured RED on 2026-09-02: 17 of 30 rows, including all eight largest
     communities. Now MIN is restricted to basenames starting with a lowercase
     letter, so dunder and _private modules can never win the column.

  2. THE COLUMN LABELLED `modules` WAS COUNTING SYMBOLS. `code_nodes` is
     symbol-level (~11.9 rows per file: 113,635 rows over 9,516 distinct .py
     files), so `COUNT(*)` overstated every domain. The largest community read
     "7295 modules" and is 700 files -- 10.4x. A domain map whose units are wrong
     mis-ranks the domains it exists to rank. Now `COUNT(DISTINCT source_file)`,
     with the symbol count kept beside it as the basis (R5) rather than dropped.

WHAT WAS *NOT* WRONG, because it was measured rather than assumed: vendored code
is not in this graph. `site-packages` = 0 rows, `node_modules` = 0 rows, absolute
paths = 0 rows; every `source_file` is repo-relative (`app/...`, `breaker_actions/...`).
An earlier draft of this fix carried a vendored-path exclusion; it was a no-op and
was removed rather than left in looking load-bearing.

`--self-test` is the negative control this tool shipped without: it re-asserts both
properties against the live bus and exits 1 on the old behaviour, 2 (UNKNOWN, not a
pass and not a failure) when the bus is unreachable.

NOTE this repairs the READING, not the WIRING. Nothing calls this tool yet, and a
correct tool that stays dark is still dark. Wiring it into the architect prompt is
the directive-architect lane's surface, not this one's.
"""
import json, sys, urllib.request

BUS = "http://127.0.0.1:8772/query"
PARKED = ("snow_connector, aidr_commit_gateway, approval_evidence_bundler, and the "
          "SNOW + AIDR external-client authorization work")

_BASENAME = "regexp_replace(source_file, '^.*/', '')"

SQL = (
    "SELECT community, "
    # A domain's size is how many FILES it spans. code_nodes is symbol-level, so
    # COUNT(*) is ~12x this and is kept only as the stated basis.
    "COUNT(DISTINCT source_file) AS modules, "
    "COUNT(*) AS symbols, "
    # An exemplar an architect can recognise: alphabetically first basename that
    # starts with a lowercase letter, so __init__.py / __main__.py / _private.py
    # can never win the column. NULL (rendered '?') if a community has none.
    f"MIN(CASE WHEN regexp_matches({_BASENAME}, '^[a-z]') THEN {_BASENAME} END) AS example "
    "FROM code_nodes WHERE source_file LIKE '%.py' "
    "AND source_file NOT LIKE 'directives/%' AND source_file NOT LIKE 'directives_archive/%' "
    "GROUP BY community ORDER BY modules DESC LIMIT 30"
)


def format_digest(rows) -> str:
    """Compact, budget-lean domain map (loaded into every architect prompt). PURE."""
    lines = ["DOMAIN MAP (graphify code-graph communities = the app's domains; "
             "RANGE across these, do NOT fixate on a few subjects):"]
    for r in rows or []:
        lines.append(f"- domain {r.get('community')}: {r.get('modules')} modules "
                     f"(e.g. {r.get('example') or '?'})")
    lines.append("")
    lines.append("BASIS: modules = DISTINCT .py source_file per graphify Leiden community; "
                 "symbol rows behind them = "
                 + str(sum(int(r.get("symbols") or 0) for r in rows or []))
                 + " over the top " + str(len(rows or [])) + " domains.")
    lines.append("PARKED (do NOT propose build / verify / investigate for): " + PARKED + ".")
    return "\n".join(lines)


def fetch(sql, timeout=90):
    req = urllib.request.Request(BUS, data=json.dumps({"sql": sql}).encode(),
                                 headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return (json.loads(r.read().decode("utf-8", "replace")) or {}).get("rows", [])


def self_test():
    """The negative control this tool shipped without.

    0 = both properties hold. 1 = the old behaviour is back. 2 = bus unreachable,
    which is UNKNOWN and must not read as either (R6).
    """
    try:
        rows = fetch(SQL)
    except Exception as e:
        print(f"[self-test] UNKNOWN: bus unreachable at {BUS}: {e}", file=sys.stderr)
        return 2
    if not rows:
        print("[self-test] FAIL: query returned 0 rows -- empty is not a pass", file=sys.stderr)
        return 1
    bad = [r for r in rows if str(r.get("example") or "").startswith("_")]
    named = [r for r in rows if r.get("example")]
    inflated = [r for r in rows
                if r.get("symbols") is not None and r.get("modules")
                and int(r["symbols"]) < int(r["modules"])]
    print(f"[self-test] rows={len(rows)} with-exemplar={len(named)} "
          f"underscore-exemplar={len(bad)} modules>symbols={len(inflated)}")
    ok = True
    for r in bad[:10]:
        print(f"  FAIL underscore exemplar: domain {r.get('community')} -> {r.get('example')}",
              file=sys.stderr)
        ok = False
    if not named:
        print("[self-test] FAIL: no row resolved an exemplar", file=sys.stderr)
        ok = False
    for r in inflated[:5]:
        print(f"  FAIL modules>symbols: domain {r.get('community')} "
              f"{r.get('modules')}>{r.get('symbols')} -- units are crossed", file=sys.stderr)
        ok = False
    print("[self-test] " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    if "--self-test" in sys.argv:
        return self_test()
    try:
        rows = fetch(SQL)
    except Exception as e:
        print(f"[error] could not reach bus at {BUS}: {e}", file=sys.stderr)
        return 1
    print(format_digest(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
