#!/usr/bin/env python3
"""Which LIVE processes read an over-cap bus table without a row bound? (#4003)

#4003 counts "583 unbounded row reads across 313 files". That number is a REPO
census, and 51% of this ledger is the class *the artifact you inspected is not
the artifact that runs* (HARNESS_DOCTRINE R1). So this tool starts from the
process roster and the bus, never from a repo path or a grep for the string
``information_schema``:

  * the roster comes from ``ps`` -- the only honest oracle for what executes
  * the over-cap tables come from ``count(*)`` on the live bus, as-of now
  * the cap comes from ``zo_sentinel.bus.OBSERVED_ROW_CAP``

Measured 2026-10-01 on this runtime: 54 live module files, **17** over-cap tables
(the issue said 9) and **16** unbounded non-aggregate sites in **12** live
modules. That is the number worth draining; the 583 is dominated by one-off
builder output that no process runs.

This is a MEASUREMENT, not a gate. It adds no required check and it fails nothing
-- the only non-zero exits are a broken detector (``--self-test``) and a bus it
could not read, because unknown is not zero (R6).

Usage::

    python3 tools/bus_unpaginated_census.py                 # live roster
    python3 tools/bus_unpaginated_census.py --json          # machine-readable
    python3 tools/bus_unpaginated_census.py --paths a.py b.py
    python3 tools/bus_unpaginated_census.py --self-test     # negative control
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zo_sentinel import bus  # noqa: E402

SEARCH_ROOTS = ("/home/workspace/zo_sentinel", "/home/workspace/zo_mesh")
AGGREGATE_RE = re.compile(r"\b(count|sum|avg|min|max)\s*\(", re.IGNORECASE)
LIMIT_RE = re.compile(r"\blimit\b", re.IGNORECASE)


def live_module_files(roots=SEARCH_ROOTS):
    """Module files of currently running processes. R1: ask the runtime."""
    try:
        out = subprocess.run(
            ["ps", "-eo", "args"], capture_output=True, text=True, timeout=30
        ).stdout
    except Exception as exc:                       # noqa: BLE001
        raise RuntimeError("cannot read the process roster: %s" % exc) from None
    found = set()
    for line in out.splitlines():
        for tok in line.split():
            if tok.endswith(".py") and os.path.isabs(tok) and os.path.exists(tok):
                found.add(os.path.realpath(tok))
        for mod in re.findall(r"-m\s+([\w\.]+)", line):
            rel = mod.replace(".", os.sep) + ".py"
            for root in roots:
                cand = os.path.join(root, rel)
                if os.path.exists(cand):
                    found.add(os.path.realpath(cand))
    return sorted(found)


def over_cap_tables(url=None):
    """Tables whose row count exceeds the bus cap, as-of NOW. Raises if unread."""
    tables = [
        r["table_name"]
        for r in bus.query_complete(
            "SELECT table_name FROM information_schema.tables ORDER BY table_name",
            url=url, what="table names",
        )
    ]
    out = {}
    for name in tables:
        if not re.fullmatch(r"\w+", name):
            continue
        rows = bus.query("SELECT count(*) AS n FROM %s" % name, url=url)
        n = int(rows[0]["n"]) if rows else 0
        if n > bus.OBSERVED_ROW_CAP:
            out[name] = n
    return out


def _statements(tree):
    """Reconstruct WHOLE statements, not SQL fragments.

    The first revision of #4003's census judged SQL fragment-by-fragment and
    reported the graph readers as unbounded when every one of them sets an
    explicit LIMIT -- a correction that had to be published on #3994. Implicit
    adjacent-literal concatenation, f-strings and ``+`` chains are therefore all
    joined before anything is matched; an interpolated expression becomes an
    opaque placeholder rather than disappearing.
    """
    out = []

    def flatten(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(
                v.value if isinstance(v, ast.Constant) and isinstance(v.value, str)
                else " ?EXPR? "
                for v in node.values
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return flatten(node.left) + flatten(node.right)
        return " ?EXPR? "

    class V(ast.NodeVisitor):
        def visit_BinOp(self, node):
            if isinstance(node.op, ast.Add):
                joined = flatten(node)
                if "select" in joined.lower():
                    out.append((node.lineno, joined))
                    return
            self.generic_visit(node)

        def visit_Constant(self, node):
            if isinstance(node.value, str):
                out.append((node.lineno, node.value))

        def visit_JoinedStr(self, node):
            out.append((node.lineno, flatten(node)))

    V().visit(tree)
    return out


def scan_file(path, tables):
    """Unbounded, non-aggregate reads of a table in ``tables``, with line numbers."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError):
        return []
    hits = []
    for lineno, raw in _statements(tree):
        sql = " ".join(raw.split())
        low = sql.lower()
        if "select" not in low:
            continue
        for table in tables:
            if not re.search(r"(from|join)\s+" + re.escape(table) + r"\b", low):
                continue
            if LIMIT_RE.search(low) or AGGREGATE_RE.search(low):
                break
            hits.append({"line": lineno, "table": table, "sql": sql[:200]})
            break
    return hits


def census(paths, tables):
    return {p: h for p in paths for h in [scan_file(p, tables)] if h}


FIXTURE_UNBOUNDED = (
    'rows = ws_query("SELECT server_id, score FROM mcp_signal_scores "\n'
    '                "WHERE server_id = ? ORDER BY signal_name")\n'
)
FIXTURE_BOUNDED = (
    'rows = ws_query("SELECT server_id, score FROM mcp_signal_scores "\n'
    '                "WHERE server_id = ? ORDER BY scored_at DESC LIMIT 1")\n'
    'n = ws_query("SELECT count(*) AS n FROM mcp_signal_scores")\n'
)


def self_test():
    """Both poles. A detector only ever seen green is UNPROVEN, not passing (R4)."""
    import tempfile

    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        bad = os.path.join(tmp, "unbounded_fixture.py")
        good = os.path.join(tmp, "bounded_fixture.py")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write(FIXTURE_UNBOUNDED)
        with open(good, "w", encoding="utf-8") as fh:
            fh.write(FIXTURE_BOUNDED)
        tables = ["mcp_signal_scores"]

        hits = scan_file(bad, tables)
        print("POLE 1 must be RED  (unbounded fixture): %d hit(s)" % len(hits))
        if len(hits) != 1:
            print("  FAIL: the detector cannot see the defect it exists to find")
            ok = False

        hits = scan_file(good, tables)
        print("POLE 2 must be GREEN (LIMIT + count(*)) : %d hit(s)" % len(hits))
        if hits:
            print("  FAIL: flagged a bounded read -- %r" % (hits,))
            ok = False

    print("SELF-TEST %s" % ("PASS -- both poles observed" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--paths", nargs="*", help="scan these files instead of the roster")
    ap.add_argument("--url", help="bus /query endpoint (default: $ZO_WRITE_SERVICE)")
    ap.add_argument("--self-test", action="store_true", help="run the two poles and exit")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    try:
        tables = over_cap_tables(url=args.url)
    except Exception as exc:                        # noqa: BLE001
        print("UNKNOWN, not zero: could not read the bus -- %s" % exc, file=sys.stderr)
        return 2

    paths = args.paths or live_module_files()
    found = census(paths, list(tables))
    sites = sum(len(v) for v in found.values())

    if args.json:
        print(json.dumps({
            "basis": {
                "roster": "ps -eo args" if not args.paths else "--paths",
                "observed_row_cap": bus.OBSERVED_ROW_CAP,
                "over_cap_tables": tables,
                "files_scanned": len(paths),
            },
            "sites": sites,
            "files": found,
        }, indent=2, sort_keys=True))
        return 0

    print("BASIS  roster=%s  files=%d  cap=%d rows  over-cap tables=%d  (as-of now)"
          % ("ps" if not args.paths else "--paths", len(paths),
             bus.OBSERVED_ROW_CAP, len(tables)))
    print("UNBOUNDED NON-AGGREGATE SITES: %d across %d file(s)" % (sites, len(found)))
    for path in sorted(found):
        print("\n  " + path)
        for hit in found[path]:
            print("    L%-5d %-26s %s" % (hit["line"], hit["table"], hit["sql"][:110]))
    if not found:
        print("  none -- and that is only as true as the roster above")
    return 0


if __name__ == "__main__":
    sys.exit(main())
