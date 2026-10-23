#!/usr/bin/env python3
"""
builder_mcp.py - FastMCP bridge: provenance + code-graph tools for Goose.
Single file. No imports of builder internals. Just a typed HTTP relay.

Goose writes files itself (developer text_editor) and records them via
register_build; this bridge emits the build_artifact provenance row and exposes
the code-graph query tools (graph_neighbors / graph_path). The legacy
delegate_to_builder single-shot codegen path (MiniMax via ladder shim -> 8796)
was retired -- goose no longer delegates code generation.
"""
import ast
import os
import sys

import httpx
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, "/home/workspace/zo_sentinel")  # for the zo_sentinel package
from zo_sentinel.build_routing import build_artifact_row  # noqa: E402

mcp = FastMCP("Zo Sentinel Builder Bridge")

WRITE_SERVICE = "http://127.0.0.1:8772"


async def _emit_build_artifact(client, target_file, content, context_type,
                               tier, model, backend):
    """Emit a build_artifact mesh row so the ingestor / governor / publisher see
    the LIVE goose build (the legacy zo_sentinel_builder feed is frozen). Best-
    effort: a write_service hiccup must never fail the build itself."""
    row = build_artifact_row(
        file=target_file, content_bytes=len(content), context_type=context_type,
        tier=tier, model=model, backend=backend,
        phase=os.environ.get("ZO_BUILD_PHASE", ""),
        task=os.environ.get("ZO_BUILD_TASK", ""),
    )
    try:
        await client.post(f"{WRITE_SERVICE}/write",
                          json={"table": "mesh_memory", "rows": [row], "wait": True})
    except Exception:
        pass


def _reject_unparseable_py(target_file: str, content: str) -> str:
    """Return a REGISTER_ERROR string if a .py artifact does not parse, else "".

    FU-565. register_build's docstring has always ASKED the caller to register
    a file "ONLY after ... `python -m py_compile` passed". It was asked and not
    done. Measured 2026-09-29 on the live build host: 20 of 458 .py files in
    the promoted tier do not parse, and 15 of those are HTML documents saved
    under a .py name -- `<!DOCTYPE html>` as the first bytes of router.py.
    They were registered through this hook and published into that tier, which
    is the source of truth app/_spine_generated.py is generated from.

    A sentence asking an agent to verify is not a verification, so the check
    lives here, at the one choke point every build artifact passes through.
    Pure function -- no I/O, no network -- so a test can exec this source with
    the stdlib alone and observe it reject a real payload.
    """
    norm = target_file.replace("\\", "/")
    if not norm.endswith(".py"):
        return ""
    try:
        compile(content, norm, "exec")
    except (SyntaxError, ValueError) as exc:
        head = " ".join(content.lstrip()[:80].split())
        lineno = getattr(exc, "lineno", None)
        where = f"line {lineno}: " if lineno else ""
        if head[:1] == "<":
            hint = ("The first bytes are markup, not Python -- if this is a "
                    "dashboard or a view, write the HTML to a template file and "
                    "have router.py serve it. Do not save markup under a .py "
                    "name. ")
        else:
            hint = ("Run `python -m py_compile` on the file and fix the syntax "
                    "error before registering it. ")
        return (f"REGISTER_ERROR: {target_file} does not parse as Python "
                f"({where}{exc}). First bytes: {head[:80]!r}. " + hint +
                "A .py artifact is not registered until it compiles. (FU-565)")
    return ""


def _load_referent_resolver(root: str = "/home/workspace/zo_sentinel"):
    """Return (catalog, iter_sql, extract_refs, reason). NEVER raises.

    The resolver is `tools/referent_verify.py` itself -- the single judge this
    repo already uses in CI -- loaded by file path rather than re-implemented.
    Two enforcement points, one definition: an emission gate carrying its own
    copy of the catalog rules would drift from the judge and start refusing
    builds the judge would pass, which is how a gate gets switched off.

    On ANY failure (tool absent, catalog stale, snapshot unreadable) this
    returns an EMPTY catalog with a reason. The caller must then ALLOW the
    registration and say so out loud. Fail-closed here would stop every build
    on this host the moment the bus snapshot ages out -- the exact shape
    #4080 recorded on 2026-09-29: "an armed check whose input plane silently
    ages out does not fail open, it fails closed across the entire repository."
    """
    import importlib.util
    import os as _os
    tool = _os.path.join(root, "tools", "referent_verify.py")
    if not _os.path.exists(tool):
        return {}, None, None, f"referent_verify.py not found at {tool}"
    try:
        spec = importlib.util.spec_from_file_location("_rv_for_register", tool)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_rv_for_register"] = mod
        spec.loader.exec_module(mod)
        catalog, _meta, unknown = mod.load_catalog()
    except Exception as exc:                       # noqa: BLE001
        return {}, None, None, f"{type(exc).__name__}: {exc}"
    if unknown:
        return {}, None, None, str(unknown)
    if not catalog:
        return {}, None, None, "catalog resolved to zero tables"
    return catalog, mod._iter_sql_strings, mod.extract_refs, ""


def _phantom_column_refs(content, catalog, iter_sql, extract_refs):
    """PURE. Return sorted [(table, column, real_columns)] for every qualified
    column referent in `content` whose table EXISTS on some plane and whose
    column exists on none.

    Scoped deliberately to that one case:
      * table absent from every plane -> NOT our verdict. `referent-verify`
        already enforces tables in CI; refusing here too would double-judge a
        name and turn one defect into two refusals.
      * table present, column absent  -> a reference that can never resolve.
        `SELECT s.tool_count FROM mcp_server_registry s` is perfect SQL and a
        400 from the bus, and the caller's `status_code == 200` gate turns
        that 400 into an empty list (FU-562). It cannot be caught downstream
        by anything except the report-only column check, whose backlog is 34
        days old.
    """
    if not catalog or iter_sql is None or extract_refs is None:
        return []
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return []                                  # _reject_unparseable_py owns this
    bad = {}
    for sql, _lineno in iter_sql(tree):
        _tables, cols = extract_refs(sql)
        for table, col in cols:
            real = catalog.get(table)
            if real is None or col in real:
                continue
            bad[(table, col)] = sorted(real)
    return sorted((t, c, r) for (t, c), r in bad.items())


def _reject_phantom_columns(target_file, content, catalog, iter_sql, extract_refs):
    """Return a REGISTER_ERROR string if a .py artifact names a column that
    exists on no plane, else "".

    FU-568. Measured 2026-09-29 against origin/main a7bc32da6: of 1022 builder
    .py registrations in the trailing 45 days, 10 named a column that exists on
    no plane -- 1.0% -- and every one of the 13 referents is also flagged by
    `referent_verify` (0 false positives against the judge). The most recent
    three landed on 2026-09-18, which is why the columns half of #4080 cannot
    converge by hand: the emission path refills it faster than a cycle empties
    it. The tables half got an emission-time block in #4068 and went 82 -> 0.
    Columns never got one.

    The message names the REAL columns of that table, so the refusal is a
    correction the caller can act on rather than a wall it has to route around
    (HARNESS_DOCTRINE R7). Pure function -- no I/O, no network -- so a test can
    exec this source with the stdlib alone and observe it refuse a real payload.
    """
    if not target_file.replace("\\", "/").endswith(".py"):
        return ""
    bad = _phantom_column_refs(content, catalog, iter_sql, extract_refs)
    if not bad:
        return ""
    lines = []
    for table, col, real in bad:
        shown = ", ".join(real[:12]) + (" ..." if len(real) > 12 else "")
        lines.append(f"  {table}.{col} -- {table} has no such column. "
                     f"Real columns: {shown}")
    return ("REGISTER_ERROR: " + target_file + " names " + str(len(bad)) +
            " column referent(s) that exist on no plane (bus schema, app "
            "models, or migrations):\n" + "\n".join(lines) +
            "\nA query naming a column that does not exist is valid SQL and a "
            "400 from the write-service, and a `status_code == 200` gate turns "
            "that 400 into an empty result instead of an error (FU-562). Use a "
            "real column above, or add a migration that creates the one you "
            "need, then re-register. (FU-568)")


@mcp.tool()
async def register_build(target_file: str, context_type: str) -> str:
    """Record a goose-built file as a build_artifact (provenance for the
    ingestor / governor / publisher). Call this ONCE, at the END of a build,
    ONLY after YOU (goose) wrote the file with the developer extension AND
    `python -m py_compile` passed. Since FU-565 the compile is ENFORCED here
    for .py targets, not merely requested: an unparseable .py is refused.
    Since FU-568 a .py naming a COLUMN that exists on no plane is refused
    too, with the table's real column names in the message.

    This is the Phase 1 provenance hook: goose writes the file itself and
    verifies it, then registers the verified file so the ingestor/publisher
    can see it.

    Args:
        target_file: Path written, relative to /home/workspace/zo_sentinel/
        context_type: 'enricher', 'daemon', 'schema', 'utility'
    """
    # FU-272: the service.toml `import_path` field contains `services.active.<name>.router`
    # as a FORWARD REFERENCE to where the file lives AFTER promotion -- NOT the write
    # destination. LLM agents running service_dir_from_exemplar.yaml confuse this and
    # call register_build with target_file under services/active/ instead of services/staged/.
    # The publisher then opens a PR into services/active/ with no service.toml, which
    # triggers capmap-check STRICT failures and a broken active registry entry.
    # CORRECT WRITE DESTINATION: always services/staged/<name>/<file>.
    # The promoter (promote_staged_to_active.py) handles the staged->active move.
    _norm = target_file.replace("\\", "/")
    if _norm.startswith("services/active/"):
        return (
            f"REGISTER_ERROR: target_file {target_file!r} is under services/active/ -- "
            "new service files must be written to services/staged/<name>/<file> instead. "
            "The import_path in service.toml names services.active.<name>.router as a "
            "FORWARD REFERENCE (where the file lives after promotion), not the write "
            "destination. Write to services/staged/ and let promote_staged_to_active.py "
            "handle the move. (FU-272)"
        )
    out = f"/home/workspace/zo_sentinel/{target_file}"
    if not os.path.exists(out):
        return f"REGISTER_ERROR: {target_file} not on disk -- write it first."
    with open(out) as f:
        content = f.read()
    if len(content.strip()) < 32:
        return (f"REGISTER_ERROR: {target_file} is {len(content)}b -- too small to be a "
                "real build; do not register a stub.")
    # FU-565: enforce the py_compile the docstring above only ASKS for. 15 HTML
    # documents reached the promoted tier as router.py through this hook.
    # (Keep the literal path out of this comment: the FU-272 negative control
    # in tests/ greps register_build's whole body for it and would go vacuous.)
    _bad_py = _reject_unparseable_py(target_file, content)
    if _bad_py:
        return _bad_py
    # FU-568: a column referent that exists on no plane is valid SQL, a 400
    # from the bus, and an empty result to any caller gating on 200. The
    # tables half of #4080 got an emission block and went 82 -> 0; columns
    # never did, and the builder added 16 new phantom column referents in
    # September alone. UNKNOWN NEVER REFUSES: an unresolvable catalog allows
    # the build and says so on the REGISTERED line, because fail-closed here
    # stops every build on the host the moment the snapshot ages out.
    _catalog, _iter_sql, _extract, _cat_reason = _load_referent_resolver()
    _bad_cols = _reject_phantom_columns(target_file, content, _catalog,
                                        _iter_sql, _extract)
    if _bad_cols:
        return _bad_cols
    tier = os.environ.get("ZO_BUILD_TIER", "zo-ladder-low")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await _emit_build_artifact(client, target_file, content, context_type,
                                       tier, os.environ.get("GOOSE_MODEL", ""),
                                       "goose_developer")
        _unchecked = ("" if not _cat_reason else
                      f" [referents UNCHECKED: {_cat_reason}]")
        return (f"REGISTERED: {target_file} ({content.count(chr(10))} lines, "
                f"tier={tier}, backend=goose_developer){_unchecked}")
    except Exception as e:
        return f"REGISTER_ERROR: {type(e).__name__}: {e}"


@mcp.tool()
async def read_signal_quality() -> str:
    """Read current enricher discrimination stats from the live DB.

    Async httpx (NOT sync requests): a blocking call inside a FastMCP @tool
    stalls the event loop and the Goose subprocess times out (constraint #1)."""
    sql = """
            SELECT signal_name,
                   COUNT(*) as total,
                   COUNT(DISTINCT ROUND(score,0)) as distinct_scores,
                   ROUND(AVG(score),2) as avg_score
            FROM mcp_signal_scores
            GROUP BY signal_name ORDER BY distinct_scores ASC
        """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{WRITE_SERVICE}/query", json={"sql": sql})
            return r.text if r.status_code == 200 else f"DB error: {r.status_code}"
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
async def build_success_stats(directive_type: str = "", complexity: str = "") -> str:
    """How often builds like THIS one succeed, and at which model/rung -- the
    failure-pattern matrix (Phase 4). Query it BEFORE a hard build to see whether a
    directive class historically needs more rescues or a stronger rung.

    Reads the `failure_matrix` view (aggregated build_provenance) over write_service.
    Both args are optional substring filters; omit them for the whole matrix. If no
    builds have been recorded yet the view is simply empty -- proceed without it.

    Args:
        directive_type: filter to an interface/context_type (e.g. 'enricher').
        complexity:     filter to 'low'|'medium'|'high'|'critical'.
    """
    where, params = [], []
    if directive_type:
        where.append("directive_type ILIKE ?")
        params.append(f"%{directive_type}%")
    if complexity:
        where.append("complexity ILIKE ?")
        params.append(f"%{complexity}%")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sql = ("SELECT directive_type, complexity, model, attempts, successes, "
           "success_pct, avg_rescues, last_error FROM failure_matrix" + clause +
           " ORDER BY attempts DESC LIMIT 40")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{WRITE_SERVICE}/query",
                                  json={"sql": sql, "params": params, "limit": 40})
    except Exception as e:
        return f"build_success_stats: matrix unavailable ({type(e).__name__}) -- proceed without it."
    if r.status_code != 200:
        return ("build_success_stats: failure_matrix not available yet "
                f"(HTTP {r.status_code}) -- proceed without it.")
    rows = r.json().get("rows", [])
    if not rows:
        return "build_success_stats: no builds recorded for that filter yet -- proceed without it."
    out = ["BUILD SUCCESS MATRIX (directive_type / complexity / model -> success%):"]
    for r_ in rows:
        out.append(f"  {r_['directive_type']}/{r_['complexity']} @ {r_['model']}: "
                   f"{r_['success_pct']}% ({r_['successes']}/{r_['attempts']}, "
                   f"avg_rescues={r_['avg_rescues']})"
                   + (f" last_error={r_['last_error']}" if r_.get('last_error') else ""))
    return "\n".join(out)


async def _gquery(client, sql, params=None):
    """POST a read to write_service /query. Returns the rows list, or None if the
    query failed (e.g. code_nodes not seeded yet -> 400)."""
    r = await client.post(f"{WRITE_SERVICE}/query",
                          json={"sql": sql, "params": params or [], "limit": 60})
    if r.status_code != 200:
        return None
    return r.json().get("rows", [])


_GRAPH_RELS = "('calls','imports','imports_from','uses','inherits','references')"


@mcp.tool()
async def graph_neighbors(target: str) -> str:
    """Code-graph neighborhood of a file or symbol: what it DEPENDS ON (you
    call/import these -- keep their signatures) and what DEPENDS ON IT (these
    break if you change a contract). Query this BEFORE writing so your change
    respects the existing call/import structure.

    Reads the DuckDB code graph (code_nodes/code_edges) through write_service.
    If the graph isn't seeded yet it says so -- just proceed without it.

    Args:
        target: a file name/path fragment or a symbol/label
                (e.g. 'builder_mcp.py' or 'register_build').
    """
    deps_sql = (
        "SELECT DISTINCT e.relation AS rel, n2.label AS name, n2.source_file AS file "
        "FROM code_edges e JOIN code_nodes n1 ON e.src=n1.id JOIN code_nodes n2 ON e.dst=n2.id "
        "WHERE (n1.source_file LIKE ? OR n1.norm_label LIKE ? OR n1.id = ?) "
        f"AND e.relation IN {_GRAPH_RELS} ORDER BY e.relation LIMIT 40")
    dependents_sql = (
        "SELECT DISTINCT e.relation AS rel, n1.label AS name, n1.source_file AS file "
        "FROM code_edges e JOIN code_nodes n1 ON e.src=n1.id JOIN code_nodes n2 ON e.dst=n2.id "
        "WHERE (n2.source_file LIKE ? OR n2.norm_label LIKE ? OR n2.id = ?) "
        f"AND e.relation IN {_GRAPH_RELS} ORDER BY e.relation LIMIT 40")
    like = f"%{target}%"
    p = [like, like.lower(), target]
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            deps = await _gquery(client, deps_sql, p)
            dependents = await _gquery(client, dependents_sql, p)
    except Exception as e:
        return f"graph_neighbors: graph unavailable ({type(e).__name__}) -- proceed without it."
    if deps is None and dependents is None:
        return "graph_neighbors: code graph not seeded yet -- proceed without it."
    out = [f"GRAPH NEIGHBORHOOD for '{target}':",
           "DEPENDS ON (you reference these -- keep their signatures):"]
    out += [f"  {r['rel']} -> {r['name']} ({r['file']})" for r in (deps or [])] or ["  (none found)"]
    out.append("DEPENDED ON BY (these break if you change the contract):")
    out += [f"  {r['rel']} <- {r['name']} ({r['file']})" for r in (dependents or [])] or ["  (none found)"]
    return "\n".join(out)


@mcp.tool()
async def graph_path(src: str, dst: str) -> str:
    """Shortest connection path (<=5 hops) between two files/symbols, via a
    bounded, cycle-guarded recursive traversal of the code graph. The graph is
    undirected, so this walks edges in BOTH directions -- it shows how a change
    in one place can reach another (call/import/containment chain).

    Args:
        src: source file/symbol fragment.
        dst: destination file/symbol fragment.
    """
    # Undirected: at each step move to the OTHER endpoint of any incident edge.
    # Resolve endpoints among CODE nodes only (the ~360 'rationale' annotation
    # nodes also match a bare %fragment% and would mis-resolve the target).
    nxt = "CASE WHEN e.src=r.id THEN e.dst ELSE e.src END"
    sql = (
        "WITH RECURSIVE "
        "s AS (SELECT id FROM code_nodes WHERE file_type='code' "
        "AND (id=? OR source_file LIKE ? OR norm_label LIKE ?) "
        "ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END, length(source_file) LIMIT 1), "
        "t AS (SELECT id FROM code_nodes WHERE file_type='code' "
        "AND (id=? OR source_file LIKE ? OR norm_label LIKE ?) "
        "ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END, length(source_file) LIMIT 1), "
        "reach(id, depth, path) AS ("
        "  SELECT id, 0, [id] FROM s "
        "  UNION ALL "
        f"  SELECT {nxt}, r.depth+1, list_append(r.path, {nxt}) "
        "  FROM reach r JOIN code_edges e ON (e.src=r.id OR e.dst=r.id) "
        f"  WHERE r.depth < 5 AND e.relation <> 'rationale_for' AND NOT list_contains(r.path, {nxt})) "
        "SELECT depth, path FROM reach WHERE id = (SELECT id FROM t) ORDER BY depth LIMIT 1")
    sl, dl = f"%{src}%", f"%{dst}%"
    p = [src, sl, sl.lower(), src, dst, dl, dl.lower(), dst]
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            rows = await _gquery(client, sql, p)
    except Exception as e:
        return f"graph_path: graph unavailable ({type(e).__name__})."
    if not rows:
        return (f"graph_path: no path from '{src}' to '{dst}' within 5 hops "
                "(or graph not seeded).")
    r = rows[0]
    return f"PATH {src} -> {dst} ({r.get('depth')} hops): " + " -> ".join(r.get("path", []))


if __name__ == "__main__":
    mcp.run(transport="stdio")