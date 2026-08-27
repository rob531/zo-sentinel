#!/usr/bin/env python3
"""Execution-based referent verification for the app spine.

THE GAP THIS CLOSES
    Every other gate in this repo verifies that code is WELL-FORMED. None
    verifies that what it REFERS TO exists. `SELECT ... FROM server_scores` is
    perfect Python and perfect SQL; it is also a reference to a table that
    exists on no plane, and it passed every gate for a month (audit finding B1)
    because no gate ever tried to use it.

    Static analysis cannot close this. A name that resolves to nothing is
    syntactically indistinguishable from one that resolves. So this check
    RESOLVES things: it boots the real app and mounts the real router set, and
    it resolves every table and column named in code against a real catalog.

THE VERDICT RULE
    Three outcomes, never two:

        PASS      the referent was checked and it exists
        FAIL      the referent was checked and it does not exist
        UNKNOWN   the referent could NOT be checked

    UNKNOWN is not PASS. It exits non-zero once enforcing. The audit's own
    conclusion names this as the recurring repair in this codebase --
    "make 'I could not evaluate this' distinguishable from 'this is fine', and
    make the distinction blocking" -- and every one of B1, G1 and G4 was a case
    of an instrument being right and its verdict being discarded.

WHICH PLANE A TABLE MUST EXIST ON
    Referents are checked against the UNION of every plane:

        bus       schema/bus_catalog.json  (live write-service, 44 tables)
        app       __tablename__ in app/models.py
        migration table names in migrations/versions/

    A table is satisfied if it exists on ANY plane. This is deliberately the
    weakest form of the check, and it is the form the audit's own correction
    licenses: server_scores / servers / score_runs "exist on NO plane at all --
    not among the 44 tables on the bus, not as a __tablename__ in app/models.py,
    and in no migration or schema snapshot."

    Inferring which plane each individual query targets would be stronger and
    would also generate false failures on every dual-plane helper in the tree.
    False failures are how gates get switched off. The union catches the entire
    class that B1 belongs to -- referring to something that is nowhere -- with a
    false-positive rate this check can actually defend.

COVERAGE
    Root modules AND services/active/** AND services/staged/**. Root-only
    scanning is precisely why 930+ staged services are invisible to the G4
    orphan census today; this walks them.

Usage:
    python tools/referent_verify.py                     # report-only (exit 0)
    python tools/referent_verify.py --enforce           # blocking
    python tools/referent_verify.py --json out.json
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUS_CATALOG = ROOT / "schema" / "bus_catalog.json"
MODELS = ROOT / "app" / "models.py"
MIGRATIONS = ROOT / "migrations" / "versions"

# How stale the committed catalog may be before this check refuses to render a
# verdict on tables. The host refresher runs daily; 14 days is ~14 consecutive
# misses, which is a dead daemon, not a slow one.
SNAPSHOT_MAX_AGE_DAYS = 14

# ...and how old it may be before this check starts SAYING SO while still
# rendering a verdict. The budget above is a cliff: at 14d + 1s the tables and
# columns checks stop resolving, and now that referent-verify is a REQUIRED
# status check (#4089) that is not a red census -- it is every PR on the repo
# blocked. A cliff nobody can see coming is the same shape as the 2026-04 E2E
# runner: fine, fine, fine, dead. The warn band is the week of notice.
SNAPSHOT_WARN_AGE_DAYS = 7

# Marker on the unknown_reason string that promotes it from UNKNOWN to STALE.
STALE_PREFIX = "STALE: "

SCAN_DIRS = ["app", "services/active", "services/staged", "tools"]
SCAN_ROOT_GLOB = "*.py"

SKIP_PARTS = {
    ".git", "__pycache__", "node_modules", "graphify-out", "archive",
    "directives_archive", ".venv", "venv", "site-packages", "build", "dist",
    # quarantine/ holds withdrawn emissions -- see quarantine/QUARANTINE_*.json.
    # It is already outside SCAN_DIRS and the root glob, so this is belt-and-
    # braces: it states the intent, and it keeps the skip true if a future
    # change adds quarantine/ to a scan root by accident.
    "quarantine",
}

# Catalog-ish and engine-internal names that are never user tables.
NON_TABLE_PREFIXES = (
    "information_schema", "pg_", "sqlite_", "duckdb_", "pragma_", "temp.",
)
NON_TABLE_NAMES = {
    "dual", "unnest", "generate_series", "range", "values", "read_csv",
    "read_parquet", "read_json", "read_json_auto", "read_csv_auto", "glob",
}

# A string is treated as SQL only if it BEGINS with a SQL verb (leading SQL
# comments and an opening paren allowed). This anchoring is load-bearing.
#
# The first cut of this file matched any string merely CONTAINING "select",
# "with" or "update", and then read "from X" out of it. Those are ordinary
# English words, so every prose docstring in the tree became a SQL statement:
# `from __future__` was reported as a missing table, as were "a", "active",
# "advisory" and "agentvault" -- 408 false MISSING verdicts against 41 real
# tables. A gate that cries wolf 408 times is not a strict gate, it is a gate
# somebody switches off. Real SQL literals in this codebase start with their
# verb; prose does not.
SQL_STMT = re.compile(
    r"^\s*(?:--[^\n]*\n\s*)*\(?\s*"
    r"(?:WITH|SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|"
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW|INDEX|TEMP|TEMPORARY)|"
    r"ALTER\s+TABLE|DROP\s+TABLE)\b", re.I)
# ...and it must reference something AFTER the leading verb. Searching the whole
# string instead re-matches the verb itself, which let the docstring
# """Update a server's verdict (admin-only)""" through as an UPDATE statement --
# that is where the phantom tables "a" and "an" came from.
SQL_BODY = re.compile(r"\b(?:from|join|into|set|values)\b", re.I)

# Tables the code creates for itself. A temp/staging table is a real referent
# that simply lives for the length of the statement, so referring to it is
# correct and must not be reported as missing.
CREATED_TABLE = re.compile(
    r"\bcreate\s+(?:or\s+replace\s+)?(?:temp\s+|temporary\s+)?"
    r"(?:table|view)\s+(?:if\s+not\s+exists\s+)?"
    r"([A-Za-z_][A-Za-z0-9_]*)", re.I)


# DDL qualifies on its prefix alone: "create temp table _tier_stage (...)" has no
# FROM/INTO to find, and missing it meant the staging table it declares was never
# registered as code-created -- so every later reference to it read as missing.
SQL_DDL = re.compile(r"^\s*(?:CREATE|ALTER|DROP)\b", re.I)


# Each verb must be followed by the clause that verb REQUIRES in real SQL.
# A generic "contains from/into/set" test is not enough, because English prose
# supplies those words too: the docstring
#     """Update ticket with resolution from ServiceNow."""
# begins with UPDATE and contains FROM, and was duly reported as an UPDATE of a
# table `ticket` selecting from a table `servicenow`. A real UPDATE always has
# SET. Requiring the verb's own mandatory clause removes that whole class.
VERB_REQUIRES = [
    (re.compile(r"^\s*\(?\s*UPDATE\b", re.I),      re.compile(r"\bset\b", re.I)),
    (re.compile(r"^\s*\(?\s*INSERT\s+INTO\b", re.I),
     re.compile(r"\b(?:values|select|default\s+values)\b", re.I)),
    (re.compile(r"^\s*\(?\s*WITH\b", re.I),        re.compile(r"\bas\s*\(", re.I)),
    (re.compile(r"^\s*\(?\s*SELECT\b", re.I),      re.compile(r"\bfrom\b", re.I)),
]

# English words that turn up as the token after FROM/UPDATE in prose that
# survived the checks above. Never a table name in this codebase.
STOPWORD_TABLES = {
    "the", "a", "an", "this", "that", "these", "those", "it", "them", "its",
    "each", "any", "every", "one", "two", "here", "there", "when", "if",
}


def _is_sql(s: str) -> bool:
    m = SQL_STMT.match(s)
    if not m:
        return False
    if SQL_DDL.match(s):
        return True
    for verb, required in VERB_REQUIRES:
        if verb.match(s):
            return bool(required.search(s, m.end()))
    return bool(SQL_BODY.search(s, m.end()))

# FROM/JOIN/INTO/UPDATE followed by a bare identifier (optionally schema.table).
TABLE_REF = re.compile(
    r"\b(?:from|join|insert\s+into|into|update|delete\s+from)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)",
    re.I)
# Any `name AS (` in a statement is a CTE, whatever punctuation precedes it.
# Anchoring this on WITH/comma missed every CTE after a closing paren in a
# multi-CTE statement -- churned, first_day, entry_totals and all_servers were
# all reported missing while being defined three lines above their own use.
CTE_DEF = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(", re.I)

# Reserved words that follow FROM/JOIN but are not tables.
SQL_KEYWORDS = {
    "lateral", "only", "select", "unnest", "table", "rows", "row", "distinct",
    "all", "as", "on", "using", "where", "group", "order", "limit", "offset",
    "having", "union", "except", "intersect", "returning", "set", "values",
}
QUALIFIED_COL = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")
ALIAS_DEF = re.compile(
    r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:as\s+)?"
    r"([A-Za-z_][A-Za-z0-9_]*)\b", re.I)
PR_NUM = re.compile(r"\(#(\d+)\)\s*$")


# ---------------------------------------------------------------- catalogs ---
def load_catalog() -> tuple[dict, dict, str | None]:
    """Return (tables->cols, meta, unknown_reason).

    unknown_reason is non-None when the table check CANNOT be rendered. It is
    never swallowed into an empty catalog -- an empty catalog would mark every
    table in the tree as missing, which reads as a catastrophic FAIL rather than
    the "could not evaluate" it actually is.
    """
    tables: dict[str, set[str]] = {}
    meta: dict = {"planes": []}

    # --- bus plane -----------------------------------------------------------
    if not BUS_CATALOG.exists():
        # relative_to() raises when the path is not under ROOT, which is how the
        # "catalog is missing" branch managed to raise instead of REPORTING that
        # the catalog was missing. Building an error message must never be able
        # to throw -- that turns a diagnosable UNKNOWN into a traceback.
        try:
            _where = BUS_CATALOG.relative_to(ROOT)
        except ValueError:
            _where = BUS_CATALOG
        return {}, meta, f"bus catalog missing at {_where}"
    try:
        snap = json.loads(BUS_CATALOG.read_text())
    except Exception:                              # noqa: BLE001
        return {}, meta, "bus catalog is not valid JSON"

    captured = snap.get("captured_at")
    age_days = None
    if captured:
        try:
            ts = datetime.fromisoformat(captured)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
        except Exception:                          # noqa: BLE001
            age_days = None

    meta["bus_captured_at"] = captured
    meta["bus_age_days"] = None if age_days is None else round(age_days, 2)

    if age_days is None:
        return {}, meta, "bus catalog has no readable captured_at timestamp"
    meta["bus_stale"] = age_days > SNAPSHOT_MAX_AGE_DAYS
    meta["bus_warn"] = SNAPSHOT_WARN_AGE_DAYS < age_days <= SNAPSHOT_MAX_AGE_DAYS
    if age_days > SNAPSHOT_MAX_AGE_DAYS:
        # The daemon is dead. This is the failure mode that killed the 2026-04
        # E2E runner silently; here it is loud.
        #
        # STALE IS ITS OWN STATE, NOT A FLAVOUR OF UNKNOWN.
        #   "could not evaluate" covers four different faults with four
        #   different fixes: the snapshot is absent, unparseable, undated, or
        #   OLD. Only the last one names a daemon that stopped, and only the
        #   last one carries a number that says how long ago. Collapsing it
        #   into a generic UNKNOWN throws away both. The STALE_PREFIX below is
        #   what the renderer keys on to say STALE-RED and print the age.
        return {}, meta, (
            f"{STALE_PREFIX}bus catalog is {age_days:.1f} days old "
            f"(budget {SNAPSHOT_MAX_AGE_DAYS}d, last captured {captured}) -- the "
            f"host refresher (tools/bus_catalog_guard.sh) is not running. "
            f"Tables and columns CANNOT be resolved against a dead catalog, so "
            f"this is RED, not a pass. Fix the refresher, do not raise the budget."
        )

    for t, cols in (snap.get("tables") or {}).items():
        tables.setdefault(t, set()).update(cols.keys())
    meta["planes"].append(f"bus:{len(snap.get('tables') or {})}")

    # --- app plane -----------------------------------------------------------
    if MODELS.exists():
        try:
            tree = ast.parse(MODELS.read_text(), str(MODELS))
            n = 0
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                tname, cols = None, set()
                for stmt in node.body:
                    if not isinstance(stmt, ast.Assign) or not stmt.targets:
                        continue
                    tgt = stmt.targets[0]
                    if not isinstance(tgt, ast.Name):
                        continue
                    if tgt.id == "__tablename__" and isinstance(stmt.value, ast.Constant):
                        tname = stmt.value.value
                    else:
                        cols.add(tgt.id)
                if tname:
                    tables.setdefault(tname, set()).update(cols)
                    n += 1
            meta["planes"].append(f"app_models:{n}")
        except Exception:                          # noqa: BLE001
            meta["planes"].append("app_models:UNPARSEABLE")

    # --- migration plane -----------------------------------------------------
    if MIGRATIONS.is_dir():
        n = 0
        for f in MIGRATIONS.glob("*.py"):
            try:
                txt = f.read_text(errors="replace")
            except Exception:                      # noqa: BLE001
                continue
            for m in re.finditer(
                    r"(?:create_table|drop_table|add_column|drop_column)\(\s*['\"]"
                    r"([A-Za-z_][A-Za-z0-9_]*)['\"]", txt):
                tables.setdefault(m.group(1), set())
                n += 1
        meta["planes"].append(f"migrations:{n}")

    meta["catalog_tables"] = len(tables)
    return tables, meta, None


# ------------------------------------------------------------- sql extract ---
def _iter_sql_strings(tree: ast.AST):
    """Yield whole SQL statements, not fragments.

    The audit's own G6 correction is the reason this reassembles concatenations
    and f-strings before matching: judging concatenated fragments separately
    produced a withdrawn finding. A fragment like `" FROM "` carries no table
    name, and `"SELECT * FROM " + tbl` must not be read as a reference to a
    table literally called `+`.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _is_sql(node.value):
                yield node.value, getattr(node, "lineno", 0)
        elif isinstance(node, ast.JoinedStr):
            # f-string: keep literal parts, replace interpolations with a
            # placeholder so they can never masquerade as an identifier.
            buf = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    buf.append(v.value)
                else:
                    buf.append(" \x00PARAM\x00 ")
            s = "".join(buf)
            if _is_sql(s):
                yield s, getattr(node, "lineno", 0)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            parts, ok = [], True
            for side in (node.left, node.right):
                if isinstance(side, ast.Constant) and isinstance(side.value, str):
                    parts.append(side.value)
                else:
                    parts.append(" \x00PARAM\x00 ")
                    ok = False
            s = "".join(parts)
            if ok is False and _is_sql(s):
                yield s, getattr(node, "lineno", 0)


# SQL COMMENTS ARE NOT REFERENTS.
#
# Found 2026-08-27 (#4080): `community` and `graph_table` were carried on the
# phantom-table list for weeks. Neither is a table anybody named. Both come out
# of tools/build_app_graph.py:118, from inside a SQL string, from lines that are
# SQL COMMENTS:
#
#     --   INSTALL duckpgq FROM community; LOAD duckpgq;
#     --   -- FROM GRAPH_TABLE (app
#
# TABLE_REF matched "FROM community" and "FROM GRAPH_TABLE" in commented-out
# documentation and reported both as tables that exist on no plane. They are
# exactly as real as the tables in a docstring, which this file already spent a
# whole regex generation learning to exclude (see SQL_STMT above, 408 false
# MISSING verdicts).
#
# This matters more now than it did while the check was report-only. A false
# MISSING on an ARMED, REQUIRED check does not annoy somebody reading a census;
# it blocks a merge on a referent that was never named, and that is precisely
# how a correct gate earns itself an off switch.
#
# Stripping is quote-aware: a `--` or `/*` inside a string literal is data, not
# a comment. Whitespace is substituted in place (newlines kept) so every offset
# and line number downstream is unchanged.
def strip_sql_comments(sql: str) -> str:
    """Blank out -- line comments and /* */ block comments, preserving offsets."""
    out = list(sql)
    i, n = 0, len(sql)
    quote = None                       # "'" or '"' while inside a literal
    while i < n:
        c = sql[i]
        if quote:
            if c == quote:
                # doubled quote is an escaped quote, still inside the literal
                if i + 1 < n and sql[i + 1] == quote:
                    i += 2
                    continue
                quote = None
            elif c == "\\" and i + 1 < n:
                i += 2
                continue
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            i += 1
            continue
        if c == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if c == "/" and i + 1 < n and sql[i + 1] == "*":
            j = sql.find("*/", i + 2)
            end = n if j == -1 else j + 2
            for k in range(i, end):
                if out[k] != "\n":
                    out[k] = " "
            i = end
            continue
        i += 1
    return "".join(out)


def extract_refs(sql: str) -> tuple[set[str], set[tuple[str, str]]]:
    """Return (tables, qualified_columns) from one reconstructed statement."""
    sql = strip_sql_comments(sql)
    ctes = {m.group(1).lower() for m in CTE_DEF.finditer(sql)}
    aliases = {m.group(2).lower(): m.group(1).lower()
               for m in ALIAS_DEF.finditer(sql)
               if m.group(2).lower() not in {"on", "where", "using", "as",
                                             "inner", "left", "right", "outer",
                                             "join", "group", "order", "limit"}}

    tables: set[str] = set()
    for m in TABLE_REF.finditer(sql):
        raw = m.group(1)
        low = raw.lower()
        if "\x00" in raw or low in ctes or low in NON_TABLE_NAMES or low in SQL_KEYWORDS \
                or low in STOPWORD_TABLES:
            continue
        if low.startswith(NON_TABLE_PREFIXES):
            continue
        if "." in low:                             # schema-qualified: take leaf
            low = low.split(".")[-1]
        if low in ctes or low in NON_TABLE_NAMES:
            continue
        tables.add(low)

    cols: set[tuple[str, str]] = set()
    for m in QUALIFIED_COL.finditer(sql):
        owner, col = m.group(1).lower(), m.group(2).lower()
        if owner.startswith(NON_TABLE_PREFIXES) or owner in ctes:
            continue
        owner = aliases.get(owner, owner)
        if owner in ctes or owner in NON_TABLE_NAMES or owner not in tables:
            continue
        cols.add((owner, col))
    return tables, cols


def scan_tree() -> tuple[dict, dict, list[dict], int]:
    """Walk the tree; return (tables, columns, unparseable, n_files, created)."""
    files: list[Path] = sorted(ROOT.glob(SCAN_ROOT_GLOB))
    for d in SCAN_DIRS:
        p = ROOT / d
        if p.is_dir():
            files.extend(sorted(p.rglob("*.py")))

    table_sites: dict[str, list[str]] = {}
    column_sites: dict[tuple[str, str], list[str]] = {}
    created: set[str] = set()
    unparseable: list[dict] = []
    seen: set[Path] = set()
    n = 0

    for f in files:
        if f in seen or any(part in SKIP_PARTS for part in f.parts):
            continue
        seen.add(f)
        n += 1
        try:
            tree = ast.parse(f.read_text(errors="replace"), str(f))
        except SyntaxError as exc:
            # A module we cannot parse is a referent we cannot check. Recorded
            # as UNKNOWN, never dropped -- silent drops are how coverage rots.
            unparseable.append({"file": str(f.relative_to(ROOT)),
                                "line": exc.lineno or 0})
            continue
        except Exception:                          # noqa: BLE001
            unparseable.append({"file": str(f.relative_to(ROOT)), "line": 0})
            continue

        rel = str(f.relative_to(ROOT))
        for sql, lineno in _iter_sql_strings(tree):
            # Comment-stripped for the CREATE scan too: a commented-out CREATE
            # does not create anything, and counting it would mark a genuinely
            # missing table as code-created -- a false PASS, the other direction
            # of the same bug.
            for cm in CREATED_TABLE.finditer(strip_sql_comments(sql)):
                created.add(cm.group(1).lower())
            tabs, cols = extract_refs(sql)
            for t in tabs:
                table_sites.setdefault(t, []).append(f"{rel}:{lineno}")
            for c in cols:
                column_sites.setdefault(c, []).append(f"{rel}:{lineno}")

    return table_sites, column_sites, unparseable, n, created


# ------------------------------------------------------------------ routes ---
def _walk_routes(routes, seen: set, out: list) -> None:
    """Flatten app.routes to leaf routes, descending through containers.

    Not every entry in app.routes is a route. Depending on the FastAPI version,
    including a router can leave a WRAPPER object there -- newer versions put
    `_IncludedRouter` in the list, which holds the real routes on an inner
    attribute and exposes no `.endpoint` of its own.

    The first version of this function assumed every entry had `.endpoint` and
    duly reported 30 unresolved routes on a GitHub runner while reporting zero
    on this host, purely because the two had different FastAPI versions. That is
    a false failure of exactly the kind that gets a gate switched off, and it
    was only ever visible by RUNNING the check in the environment that would run
    it -- which is the whole argument of this file.
    """
    for r in routes:
        if id(r) in seen:
            continue
        seen.add(id(r))
        sub = getattr(r, "routes", None)
        if sub is None:
            for attr in ("original_router", "included_router"):
                inner = getattr(r, attr, None)
                if inner is not None and getattr(inner, "routes", None) is not None:
                    sub = inner.routes
                    break
        if sub:
            _walk_routes(sub, seen, out)
            continue
        out.append(r)


def check_routes() -> dict:
    """Boot the real app and mount the real router set.

    This is the execution half. Importing app.main runs include_spine(), which
    is where a router that names a module that is not there actually fails --
    and app/main.py mounts "best-effort ... never block boot", so the failures
    are recorded on app.state rather than raised. Reading them back is the only
    way to see them.
    """
    res: dict = {"verdict": "UNKNOWN", "detail": "", "routes": 0,
                 "mounted": 0, "skipped_no_router": [], "failures": []}
    sys.path.insert(0, str(ROOT))
    cwd = os.getcwd()
    try:
        os.chdir(ROOT)
        import app.main as appmain            # noqa: PLC0415 -- deliberate late import
    except Exception as exc:                   # noqa: BLE001
        res["detail"] = f"app.main did not import: {type(exc).__name__}"
        return res
    finally:
        os.chdir(cwd)

    app = appmain.app
    st = app.state
    res["failures"] = list(getattr(st, "spine_mount_failures", []) or [])
    res["skipped_no_router"] = list(getattr(st, "spine_skipped_no_router", []) or [])
    res["mounted"] = len(getattr(st, "spine_mounted", []) or [])
    res["service_count"] = getattr(st, "spine_service_count", 0)

    leaves: list = []
    _walk_routes(app.routes, set(), leaves)

    unresolved = []
    for r in leaves:
        ep = getattr(r, "endpoint", None) or getattr(r, "app", None)
        if ep is None or not callable(ep):
            path = getattr(r, "path", None) or type(r).__name__
            unresolved.append(str(path)[:120])
    res["routes"] = len(leaves)
    res["unresolved"] = unresolved

    # A service that declares no router is SKIPPED, not failed -- correct, and
    # the reason the routes check can be armed at all. But an unbounded skip
    # list is a hole the size of the gate: a NEW service that silently declares
    # no router would join it and the verdict would stay green. So the skip list
    # must be DECLARED, exactly as tools/reachability_deferred.json and the
    # `known` list are, and an undeclared skip is a routes FAILURE.
    #
    # The four standing skips are classified in tools/spine_known_issues.json:
    #   entity_report_exporter    NO_ROUTER -- real router is the unmounted
    #                             orphan entity_report_exporter_router
    #   org_api_key_manager       NO_ROUTER -- library module (APIKeyManager
    #                             class); exposes no `router` attribute
    #   overview_dashboard_api    NO_ROUTER -- declares `app = FastAPI()` and
    #                             `@app.get`, not an APIRouter, so include_spine
    #                             cannot mount it. INCOMPLETE, not by-design.
    #   verdict_watchlist_service NO_ROUTER -- library module (add_watch /
    #                             on_verdict_change); no HTTP surface
    declared: set[str] = set()
    ki = ROOT / "tools" / "spine_known_issues.json"
    ki_error = None
    if ki.exists():
        try:
            _k = json.loads(ki.read_text(encoding="utf-8"))
            declared = {e["service"] for e in _k.get("known", [])
                        if e.get("status") == "NO_ROUTER"}
        except Exception:                      # noqa: BLE001
            ki_error = "spine_known_issues.json is unreadable"
    else:
        ki_error = "tools/spine_known_issues.json is missing"

    undeclared = sorted(s for s in res["skipped_no_router"] if s not in declared)
    res["undeclared_no_router"] = undeclared

    if ki_error:
        # Cannot decide whether the skips are declared. UNKNOWN, never PASS.
        res["verdict"] = "UNKNOWN"
        res["detail"] = f"{ki_error} -- cannot verify the no-router skip list"
        return res

    if res["failures"] or unresolved or undeclared:
        bits = []
        if res["failures"]:
            bits.append(f"{len(res['failures'])} mount failure(s)")
        if unresolved:
            bits.append(f"{len(unresolved)} unresolved route(s)")
        if undeclared:
            bits.append(f"{len(undeclared)} UNDECLARED no-router service(s): "
                        f"{undeclared} -- declare them in "
                        f"tools/spine_known_issues.json with a reason, or give "
                        f"them a router")
        res["verdict"] = "FAIL"
        res["detail"] = ", ".join(bits)
    else:
        res["verdict"] = "PASS"
        res["detail"] = (f"{res['mounted']}/{res['service_count']} mounted, "
                         f"{res['routes']} routes all resolve, "
                         f"{len(res['skipped_no_router'])} declared no-router skip(s)")
    return res


# -------------------------------------------------------------- merged PRs ---
def merged_prs(hours: int) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "log", f"--since={hours} hours ago", "--format=%s"],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return []
        prs = []
        for line in out.stdout.splitlines():
            m = PR_NUM.search(line.strip())
            if m:
                prs.append(f"#{m.group(1)} {line.strip()[:70]}")
        return prs
    except Exception:                              # noqa: BLE001
        return []


# -------------------------------------------------------------------- main ---
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--enforce", action="store_true",
                    help="exit non-zero on FAIL or UNKNOWN in ANY check "
                         "(default: report-only)")
    ap.add_argument("--enforce-checks", default="",
                    help="comma-separated checks to enforce while the rest stay "
                         "report-only, e.g. --enforce-checks routes. A check "
                         "named here exits non-zero on FAIL *or* UNKNOWN.")
    ap.add_argument("--json", type=Path, help="write the structured report here")
    ap.add_argument("--since-hours", type=int, default=24)
    ap.add_argument("--summary-md", type=Path,
                    help="write a markdown verdict table (CI job summary)")
    ap.add_argument("--skip-routes", action="store_true",
                    help="skip the boot check (deps unavailable)")
    args = ap.parse_args()

    print("=" * 72)
    print("REFERENT VERIFICATION -- does what the code names actually exist?")
    print("=" * 72)

    report: dict = {"generated_at": datetime.now(timezone.utc).isoformat()}
    fails, unknowns = [], []

    # -- 1. routes ------------------------------------------------------------
    if args.skip_routes:
        routes = {"verdict": "UNKNOWN", "detail": "--skip-routes requested"}
    else:
        routes = check_routes()
    report["routes"] = routes
    print(f"\n[1] ROUTE REFERENTS .......... {routes['verdict']}")
    print(f"    {routes['detail']}")
    for f in routes.get("failures", [])[:10]:
        print(f"    FAIL mount: {f}")
    for u in routes.get("unresolved", [])[:10]:
        print(f"    FAIL unresolved route: {u}")
    if routes["verdict"] == "FAIL":
        fails.append("routes")
    elif routes["verdict"] == "UNKNOWN":
        unknowns.append(f"routes ({routes['detail']})")

    # -- 2/3. tables + columns ------------------------------------------------
    catalog, meta, cat_unknown = load_catalog()
    report["catalog"] = meta
    table_sites, column_sites, unparseable, n_files, created = scan_tree()
    report["scanned_files"] = n_files
    report["unparseable"] = unparseable

    print(f"\n    scanned {n_files} files "
          f"({', '.join(SCAN_DIRS)} + root)")
    print(f"    catalog planes: {', '.join(meta.get('planes', [])) or 'NONE'}")
    if meta.get("bus_age_days") is not None:
        _age = meta["bus_age_days"]
        _band = "OK"
        if meta.get("bus_stale"):
            _band = f"STALE -- OVER THE {SNAPSHOT_MAX_AGE_DAYS}d BUDGET"
        elif meta.get("bus_warn"):
            _band = (f"WARN -- past {SNAPSHOT_WARN_AGE_DAYS}d, "
                     f"{SNAPSHOT_MAX_AGE_DAYS - _age:.1f}d until this check goes "
                     f"STALE-RED and blocks every PR")
        print(f"    bus snapshot age: {_age}d "
              f"(warn {SNAPSHOT_WARN_AGE_DAYS}d / budget {SNAPSHOT_MAX_AGE_DAYS}d) "
              f"-- {_band}")
        if meta.get("bus_warn"):
            print(f"    ::warning:: the host refresher has not landed a snapshot "
                  f"in {_age}d. Run tools/bus_catalog_guard.sh --force on the host.")

    if cat_unknown:
        # Cannot evaluate. NOT a pass.
        #
        # A STALE snapshot is reported as its own verdict, STALE, carrying the
        # age. It is counted as a FAILURE, not an unknown: "the catalog is 19
        # days old" is not a thing we could not work out, it is a thing we
        # worked out and it is bad. UNKNOWN stays for the genuinely
        # unevaluable -- absent, unparseable, undated.
        _stale = cat_unknown.startswith(STALE_PREFIX)
        _v = "STALE" if _stale else "UNKNOWN"
        _label = "STALE-RED" if _stale else "UNKNOWN"
        _detail = cat_unknown[len(STALE_PREFIX):] if _stale else cat_unknown
        print(f"\n[2] TABLE REFERENTS .......... {_label}")
        print(f"    {_detail}")
        print(f"[3] COLUMN REFERENTS ......... {_label}")
        print(f"    {_detail}")
        report["tables"] = {"verdict": _v, "detail": _detail,
                            "bus_age_days": meta.get("bus_age_days")}
        report["columns"] = {"verdict": _v, "detail": _detail,
                             "bus_age_days": meta.get("bus_age_days")}
        if _stale:
            fails.extend(["tables", "columns"])
        else:
            unknowns.append(f"catalog ({_detail})")
    else:
        missing_t = {t: s for t, s in sorted(table_sites.items())
                     if t not in catalog and t not in created}
        print(f"\n[2] TABLE REFERENTS .......... "
              f"{'FAIL' if missing_t else 'PASS'}")
        print(f"    {len(table_sites)} distinct tables referenced, "
              f"{len(catalog)} in catalog, {len(missing_t)} MISSING")
        for t, sites in list(missing_t.items())[:25]:
            print(f"    MISSING TABLE  {t}")
            for s in sites[:3]:
                print(f"        referenced at {s}")
        report["tables"] = {
            "verdict": "FAIL" if missing_t else "PASS",
            "referenced": len(table_sites),
            "missing": {t: s[:5] for t, s in missing_t.items()},
        }
        if missing_t:
            fails.append("tables")

        missing_c = {f"{t}.{c}": s for (t, c), s in sorted(column_sites.items())
                     if t in catalog and catalog[t] and c not in catalog[t]}
        print(f"\n[3] COLUMN REFERENTS ......... "
              f"{'FAIL' if missing_c else 'PASS'}")
        print(f"    {len(column_sites)} qualified column refs checked, "
              f"{len(missing_c)} MISSING")
        for c, sites in list(missing_c.items())[:25]:
            print(f"    MISSING COLUMN {c}")
            for s in sites[:3]:
                print(f"        referenced at {s}")
        report["columns"] = {
            "verdict": "FAIL" if missing_c else "PASS",
            "checked": len(column_sites),
            "missing": {c: s[:5] for c, s in missing_c.items()},
        }
        if missing_c:
            fails.append("columns")

    # -- 4. unparseable modules ----------------------------------------------
    print(f"\n[4] PARSE COVERAGE ........... "
          f"{'UNKNOWN' if unparseable else 'PASS'}")
    print(f"    {len(unparseable)} module(s) could not be parsed "
          f"(their referents are unchecked)")
    for u in unparseable[:10]:
        print(f"    UNPARSEABLE {u['file']}:{u['line']}")
    if unparseable:
        unknowns.append(f"{len(unparseable)} unparseable module(s)")

    # -- 5. merged PRs --------------------------------------------------------
    prs = merged_prs(args.since_hours)
    report["merged_prs"] = prs
    print(f"\n[5] PRs MERGED IN LAST {args.since_hours}h ... {len(prs)}")
    for p in prs[:15]:
        print(f"    {p}")
    if len(prs) > 15:
        print(f"    ... and {len(prs) - 15} more")
    print("    (a green verdict above covers these; a red one implicates them)")

    # -- verdict --------------------------------------------------------------
    if fails:
        verdict, code = "FAIL", 1
    elif unknowns:
        verdict, code = "UNKNOWN", 2
    else:
        verdict, code = "PASS", 0
    report["verdict"] = verdict

    print("\n" + "=" * 72)
    print(f"VERDICT: {verdict}")
    if fails:
        print(f"  FAILED: {', '.join(fails)}")
    for u in unknowns:
        print(f"  UNKNOWN: {u}")
    if verdict == "UNKNOWN":
        print("  UNKNOWN is not PASS. Something could not be evaluated.")
    print("=" * 72)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"\nreport -> {args.json}")

    if args.summary_md:
        args.summary_md.parent.mkdir(parents=True, exist_ok=True)
        r, t, c = (report.get("routes", {}), report.get("tables", {}),
                   report.get("columns", {}))
        # The headline must be the ARMED verdict, not the overall one.
        #
        # This job exits 0 while tables/columns are report-only, so a summary
        # leading with "VERDICT: FAIL" tells a reader the gate is broken when
        # the run is green -- and the whole point of this page is that nobody
        # has to read code to see whether the gate is working. Lead with what
        # decides the job's exit status; keep the full verdict below it.
        armed_names = [c.strip() for c in (args.enforce_checks or "").split(",")
                       if c.strip()]
        if args.enforce:
            headline = f"**{verdict}** -- every check is armed"
        elif armed_names:
            bad = [c for c in armed_names
                   if report.get(c, {}).get("verdict") in ("FAIL", "UNKNOWN", "STALE")]
            headline = (
                f"**ARMED CHECKS FAILING: {', '.join(bad)}** -- this job is RED"
                if bad else
                f"**ARMED CHECKS PASS ({', '.join(armed_names)})** -- this job is GREEN"
            )
        else:
            headline = f"**REPORT-ONLY** -- this job is GREEN regardless (overall {verdict})"

        md = [
            "## Referent verification", "",
            headline, "",
            f"<sub>full verdict across all checks, armed or not: <b>{verdict}</b></sub>", "",
            "| check | result |", "|---|---|",
            f"| routes | {r.get('verdict')} -- {r.get('detail','')} |",
            f"| tables | {t.get('verdict')} -- {len(t.get('missing', {}))} missing "
            f"of {t.get('referenced', '?')} referenced |",
            f"| columns | {c.get('verdict')} -- {len(c.get('missing', {}))} missing "
            f"of {c.get('checked', '?')} checked |",
            f"| files scanned | {report.get('scanned_files', '?')} |",
            f"| unparseable modules | {len(report.get('unparseable', []))} |",
            f"| bus snapshot age (days) | "
            f"{report.get('catalog', {}).get('bus_age_days', '?')} "
            f"(warn {SNAPSHOT_WARN_AGE_DAYS}d / budget {SNAPSHOT_MAX_AGE_DAYS}d)"
            f"{' **STALE-RED**' if report.get('catalog', {}).get('bus_stale') else ''}"
            f"{' **WARN**' if report.get('catalog', {}).get('bus_warn') else ''} |",
            f"| PRs merged in window | {len(report.get('merged_prs', []))} |",
            f"| no-router skips (declared) | "
            f"{len(r.get('skipped_no_router', []))} |",
            f"| no-router skips (UNDECLARED) | "
            f"{len(r.get('undeclared_no_router', []))} |",
            "",
            ("**routes is ARMED** -- a FAIL or UNKNOWN there fails this job. "
             "tables and columns are REPORT-ONLY: they are red on a backlog of "
             "pre-2026-08-11 emissions, not on current output. See issue #4032."),
        ]
        args.summary_md.write_text("\n".join(md) + "\n")

    if args.enforce:
        return code

    # Partial arming. The routes half of this check is SOLVED (PASS: all
    # services mounted or declared, every route resolving); the tables/columns
    # half is FAIL on a historical backlog of pre-gate emissions that predates
    # the 2026-08-11 grounding fix. Waiting for that backlog means the gate is
    # never armed at all -- which is precisely how the July mount-point fix sat
    # correct-but-unreached until August. So the solved half is locked in now
    # and the rest stays report-only.
    #
    # UNKNOWN enforces exactly like FAIL for an armed check. That is the whole
    # design rule of this tool: "could not evaluate" is not "fine".
    armed = [c.strip() for c in args.enforce_checks.split(",") if c.strip()]
    if armed:
        bad = []
        for c in armed:
            v = report.get(c, {}).get("verdict")
            if v is None:
                bad.append(f"{c} (no such check)")
            elif v in ("FAIL", "UNKNOWN", "STALE"):
                # STALE enforces exactly like FAIL. A check cannot pass on a
                # catalog it has already measured as dead.
                bad.append(f"{c}={v}")
        print("\n" + "=" * 72)
        print(f"ARMED CHECKS: {', '.join(armed)}  "
              f"(all others report-only)")
        if bad:
            print(f"ENFORCED FAILURE: {', '.join(bad)}")
            print("=" * 72)
            return 1
        print("armed checks all PASS -- exiting 0 "
              f"(unarmed checks would have exited {code} under --enforce)")
        print("=" * 72)
        return 0

    print("\nREPORT-ONLY: exiting 0 regardless "
          f"(would have exited {code} under --enforce)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
