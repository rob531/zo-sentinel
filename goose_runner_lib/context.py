# goose_runner_lib/context.py -- PART OF goose_runner.py, not a standalone module.
# task spec, build context, model roster, SOA/recipe helpers, task execution.
# goose_runner.py runs this file via _goose_runner_lib_exec("context") INSIDE
# goose_runner's own namespace, at the exact position this code stood
# (original lines 473-1414), so every name here is still
# goose_runner.<name> and __file__ is goose_runner.py.
# Do not import it directly; callers keep citing goose_runner.<name>.
# (chain rebase_godfiles S20, 2026-11-02; map: goose_runner_lib/README.md)
from __future__ import annotations

# =============================================================================
# GOOSE EXECUTION
# =============================================================================

def write_task_spec(directive):
    """Write directive spec to task file for Goose."""
    directive_id = directive.get("directive_id") or directive.get("id", "unknown_" + str(time.time()))
    content = directive.get("content", directive.get("description", ""))
    
    task_spec = {
        "directive_id": directive_id,
        "description": directive.get("description", content[:200]),
        "content": content,
        "complexity": directive.get("complexity", "low"),
        "source": "goose_tier1",
        "created_at": get_utc_now()
    }
    
    TASK_FILE.write_text(json.dumps(task_spec, indent=2))
    log(f"Task spec written to {TASK_FILE}")
    return directive_id

def check_goose_installed():
    """Check if Goose CLI is available."""
    try:
        result = subprocess.run(
            [GOOSE_BIN, "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            log(f"Goose version: {result.stdout.strip()}")
            return True
    except FileNotFoundError:
        log("Goose CLI not found in PATH")
    except Exception as e:
        log(f"Goose check failed: {e}")
    return False

def _graph_context(directive):
    """Best-effort code-graph neighborhood for the directive's target file,
    folded into the task so even a weak rung that ignores the graph_neighbors
    tool still gets structure. Pure read via ws_query (the architect's DuckDB
    copy of the graph). Returns '' on ANY error -- e.g. code_nodes not seeded --
    so it can never block or fail a build. No new dependency, no DB write."""
    try:
        out = declared_output(directive)
        if out is None:
            return ""
        fname = str(out.name).replace("'", "''")   # basename; escape quotes (ws_query has no params)
        if not fname:
            return ""
        res = ws_query(
            "SELECT DISTINCT e.relation AS rel, n1.label AS who, n1.source_file AS file "
            "FROM code_edges e JOIN code_nodes n1 ON e.src=n1.id JOIN code_nodes n2 ON e.dst=n2.id "
            f"WHERE n2.source_file LIKE '%{fname}' "
            "AND e.relation IN ('calls','imports','imports_from','uses','inherits') LIMIT 20")
        rows = res.get("rows", []) if isinstance(res, dict) else []
        if not rows:
            return ""
        lines = [f"  - {r.get('who')} ({r.get('file')}) {r.get('rel')} it" for r in rows]
        return ("GRAPH CONTEXT -- existing code that depends on " + str(out.name) +
                " (do NOT break these contracts):\n" + "\n".join(lines))
    except Exception:
        return ""


def _lessons_context(directive):
    """Closed-loop READER: fold any OPEN lesson(s) for this directive's target into
    the build task so goose sees WHY a prior attempt ghosted and fixes the root
    cause instead of repeating it. Sits beside _graph_context in build-task
    assembly. File-based (open_lessons_for) -> zero DB load on the build hot path
    (the 'zo_db_query destabilizes write_service' rule). Returns '' on any miss so
    it can never block a build."""
    try:
        out = declared_output(directive)
        subject = (out.name if out is not None
                   else (directive.get("directive_id") or directive.get("id") or ""))
        if not subject:
            return ""
        # PRIMARY: functional / schema lessons -- these BLOCK and must be fixed first.
        primary = format_lessons_context(open_lessons_for(LESSONS_DIR, subject), subject)
        # SECONDARY: a non-blocking security advisory, appended LAST so functionality
        # and schema-binding lead the model's attention (security is the 2nd goal).
        sec = open_lessons_for(LESSONS_DIR, f"{subject}::security")
        sec_txt = ""
        if sec:
            _obs = str(sec[0].get("observation", ""))[:300]
            sec_txt = ("\n\nSECONDARY (security advisory, NON-BLOCKING -- do not sacrifice "
                       "the working feature or schema-binding to satisfy this): " + _obs)
        return (primary + sec_txt).strip()
    except Exception:
        return ""


# (mtime, names) for the app.models roster; see _model_roster().
_MODEL_ROSTER_MEMO = {"mtime": None, "names": None}


def _model_roster():
    """(names, klass) -- the COMPLETE app.models class roster, from the same
    schema KL the schema-PRM gate lints against.

    WHY THIS EXISTS. _data_access_context named FOUR model classes as spelling
    examples and otherwise pointed the model at docs/SCHEMA_TRUTH.md -- a file
    a single-shot chat completion with no filesystem cannot open. That is the
    same unfollowable-pointer defect the 2026-08-11 grounding fix removed from
    the engine prompt, still live in the block that is folded into EVERY build
    task. Measured on the live runner's log (basis: /home/workspace/logs/
    goose_runner.log, 2026-09-16T10:58Z..2026-09-20T06:24Z, the window the file
    covers): 145 ghost-guard give-ups, 95 of them gate=selftest, and 77 lines
    of the form "'X' is not in 'Y'". The app.models half of those names --
    ServiceHealth, OrgService, McpRiskRegister, MCPSignalScores, MeshMemory,
    McpThreatAssociation, McpSignalScore -- are all absent from a 14-class
    roster. Every one is plausible. None is on the list, and the list was
    never shown.

    14 names is about 200 characters: the same trade _bus_table_context()
    already makes for the 45 bus tables, applied to the plane that carries the
    largest measured share of the failures.

    klass is "ok" or "kl_error" and the roster is NEVER [] with klass "ok".
    An unreadable KL and a schema with no models are different facts (R6), and
    a caller must not print "the COMPLETE roster is: <nothing>" when the truth
    is unknown -- that would assert a falsehood with more authority than the
    pointer it replaced.
    """
    path = PROJECT_DIR / "graphify-out" / "schema_kl.json"
    try:
        mtime = path.stat().st_mtime
    except Exception:                                      # noqa: BLE001
        mtime = None
    if (_MODEL_ROSTER_MEMO["names"] is not None
            and _MODEL_ROSTER_MEMO["mtime"] == mtime):
        return _MODEL_ROSTER_MEMO["names"], "ok"
    try:
        kl, _ = _schema_kl_cached()
    except Exception:                                      # noqa: BLE001
        return [], "kl_error"
    names = sorted(n for n in ((kl or {}).get("models") or {}) if n)
    if not names:
        return [], "kl_error"
    _MODEL_ROSTER_MEMO["mtime"] = mtime
    _MODEL_ROSTER_MEMO["names"] = names
    return names, "ok"


# Keyed on the rendered roster, not a bare None, so a regenerated KL re-renders
# instead of serving a stale block for the life of the daemon.
_DATA_ACCESS_CTX = None
_DATA_ACCESS_KEY = None


def _data_access_context(directive):
    """Proactive DATA-ACCESS grounding folded into EVERY build task (beside
    _graph_context / _lessons_context). MCP scores, signals, verdicts and the server
    registry live in the DATABASE (Postgres / DuckDB behind write_service), NOT in
    files -- read via the write_service /query endpoint, never a CSV. Pre-empts the
    data-source hallucination on attempt 1 (schema-PRM only catches it reactively).
    Table list derived from the live schema KL so it stays real. Cached per process;
    never raises.

    NOTE (2026-08-11): the table list below is a HARDCODED LITERAL. An earlier
    version of this docstring claimed it was "derived from the live schema KL
    so it stays real"; it never was, and that sentence is why nobody looked
    for the missing column truth for weeks. Real per-directive columns come
    from _schema_ground_context(); this block carries table/class names and
    the import contract only.

    2026-09-20: the CLASS half of that is now genuinely derived -- the complete
    app.models roster is inlined from the schema KL by _model_roster(). The
    docs/SCHEMA_TRUTH.md pointer it replaces named an authority the engine (a
    single chat completion, no filesystem) cannot consult, and a model sent to
    fetch truth it cannot reach invents it. The TABLE list in plane (1)/(2)
    below is still a literal and is still not claimed to be derived."""
    global _DATA_ACCESS_CTX, _DATA_ACCESS_KEY
    _roster, _rklass = _model_roster()
    _key = (_rklass, tuple(_roster))
    if _DATA_ACCESS_CTX is not None and _DATA_ACCESS_KEY == _key:
        return _DATA_ACCESS_CTX
    _DATA_ACCESS_KEY = _key
    # R6: an unreadable KL is not an empty roster. Fall back to the exact text
    # that shipped before this change -- pointer and all -- rather than telling
    # the model that no model classes exist.
    _symbols = (
        "SYMBOL TABLE: docs/SCHEMA_TRUTH.md, generated from app/models.py + app/db.py, lists "
        "every name that EXISTS -- read it before writing an import. If a model is not in it "
        "it does not exist: use the nearest real class or flag that the directive needs a "
        "schema decision, never invent one. Spelling is the commonest miss -- prefix Mcp not "
        "MCP, never plural: McpServerRegistry, McpLlmAxisScore, McpScoreDispute, VulnAdvisory. "
    ) if _rklass != "ok" else (
        "APP MODEL CLASSES -- the COMPLETE roster of names importable from app.models, read "
        "from the live schema KL (the same source the schema-PRM gate lints against). A class "
        "name that is NOT on this list DOES NOT EXIST, however plausible it looks and however "
        "confidently the directive names it: use the nearest real class, or say the directive "
        "needs a schema decision. Never invent one, and never pluralise or re-case one (Mcp, "
        "not MCP). The roster is: " + ", ".join(_roster) + ". "
    )
    _DATA_ACCESS_CTX = (
        "DATA ACCESS: data lives in databases, never files (no CSV/JSON inputs; CSV is "
        "export-only). TWO PLANES -- (1) APP tables (mcp_server_registry, mcp_llm_axis_scores, "
        "mcp_score_disputes, orgs, users) are authoritative in the app Postgres: read via the "
        "app.db SQLAlchemy session -- import it VERBATIM: from app.db import get_session and "
        "from app.models import <Model>, use Depends(get_session). Do NOT define your own "
        "session/engine/models or an in-memory DB: a FastAPI module with no from app.db import "
        "is a HOLLOW build and is REJECTED by the no-hollow gate (mirror verdict_breakdown_api.py). "
        "Do not read app tables off :8772 (stale partial copy). (2) MESH/pipeline tables (mcp_signal_scores, mesh_memory) live in the "
        "ZoComputer store: read via write_service POST http://127.0.0.1:8772/query. "
        "SELF-TEST vs DATA LAYER: if a directive's acceptance mentions an 'in-memory store', "
        "that applies ONLY to the __main__ self-test -- build a LOCAL FastAPI() there and set "
        "that_app.dependency_overrides[get_session]. There is NO app.dependency_overrides "
        "module (`app` is the PACKAGE; the instance is app.main:app), and StaticPool is NOT "
        "in app.db (from sqlalchemy.pool import StaticPool). "
        + _symbols +
        "The MODULE's OWN data access MUST remain from app.db import get_session + "
        "from app.models import <Model>; a module whose data layer is itself an in-memory/"
        "sqlite store (rather than just the test override) is HOLLOW and REJECTED. "
        "Keep output lean: minimal comments, no narration.")
    return _DATA_ACCESS_CTX


_RECIPE_ALLOW = {"webapp_backend_fastapi", "webapp_frontend_react", "webapp_fullstack", "module_from_exemplar", "architect", "service_dir_from_exemplar"}
# Conservative inference: only the auth/RBAC/tenant SECURITY SPINE (where the generic
# single-file builder produced hollow stubs) is keyword-routed to the FastAPI recipe.
# Reports/search/etc. stay on architect.yaml unless the directive sets `recipe` explicitly.
_BACKEND_HINTS = ("oauth", "login_service", "rbac", "role_enforc", "require_role",
                  "tenant", "api_key", "session", "jwt", "auth_", "_auth", "org_model",
                  # app-surface: route every app API module to the FastAPI recipe (which
                  # ENFORCES reading mcp_llm_axis_scores via the DB session) -- without this
                  # they fell through to the generic builder and shipped hollow stubs.
                  "_api", "verdict", "scoring", "_consumer", "dashboard", "registry_search",
                  "report", "compliance", "risk_register", "risk_trend", "policy", "decisions", "watchlist")
_FRONTEND_HINTS = (".html", "_dashboard.", "frontend", "_ui.", "_view.", "react")


def _select_recipe(directive):
    """Pick the goose execute-recipe for a directive. Explicit `recipe` field wins
    (allowlisted); else infer the webapp backend/frontend recipe for clear app-spine
    modules; else None -> caller defaults to architect.yaml. Backward-compatible."""
    try:
        r = str(directive.get("recipe", "") or "").strip()
        if r in _RECIPE_ALLOW and r != "architect":
            return r
        blob = " ".join(str(directive.get(k, "")) for k in
                        ("directive_id", "id", "output_file", "target_file", "title", "description")).lower()
        try:
            _o = declared_output(directive)
            if _o:
                blob += " " + str(_o).lower()
        except Exception:
            pass
        if any(h in blob for h in _FRONTEND_HINTS):
            return "webapp_frontend_react"
        if any(h in blob for h in _BACKEND_HINTS):
            return "webapp_backend_fastapi"
    except Exception:
        pass
    return None



# ----------------------------------------------------------------- SOA CANARY
# CofC 2026-08-04 (3 seats + FATHER), FU-246. service_dir_from_exemplar.yaml
# declares `service_name` and `service_spec` as requirement: required, but
# run_goose_task only ever passed `task_description`. Consequence, measured on
# /home/workspace/logs/goose_runner.log over 2026-08-03T09:09:26Z..2026-08-04T12:02Z
# (22957 lines): that recipe was routed 1082 times and failed 1082 times -- 100%,
# one identical error ("Please provide the following parameters in the command
# line: --params service_name=your_value --params service_spec=your_value").
# It is the SOA lane, selected for ~95% of directives, so the loop's declared
# atomic unit -- the SERVICE -- has never once been built by goose. Nothing went
# red because the engine fallback writes the file anyway, and the 162 tier1
# successes from task_description-only recipes kept the AGGREGATE green. That is
# HARNESS_DOCTRINE R3/R4/R6 failing together: a zero that was never distinguished
# from "not exercised".
#
# This ships as a CANARY, not a restoration: the recipe's prompt and its
# "OUT_OF_LANE: report and stop" lane guard have ZERO production exposure, so
# enabling them is a first live trial. Caps are numeric with env override;
# SOA_GOOSE_MAX_PER_DAY=0 is the kill switch and needs no revert.
_SOA_CANARY_MARKER = "cofc-2026-08-04"


def _soa_int_env(name, default):
    try:
        return max(0, int(str(os.environ.get(name, "") or default).strip()))
    except Exception:
        return default


SOA_GOOSE_MAX_PER_DAY = _soa_int_env("SOA_GOOSE_MAX_PER_DAY", 8)
SOA_GOOSE_MAX_CONCURRENT = _soa_int_env("SOA_GOOSE_MAX_CONCURRENT", 2)
SOA_RECIPE = "service_dir_from_exemplar"
_SOA_STATE_PATH = Path(
    os.environ.get("SOA_GOOSE_STATE", "/home/workspace/logs/soa_goose_canary.json"))
try:
    _SOA_SEM = threading.BoundedSemaphore(max(1, SOA_GOOSE_MAX_CONCURRENT))
except Exception:  # pragma: no cover - threading always present
    _SOA_SEM = None

_WIN_FORBIDDEN = '<>:"/\\|?*'


def _sanitise_service_name(raw):
    """FU-209: a path containing < or > makes EVERY checkout of main fail on the
    Windows tower. Applied UNCONDITIONALLY on both the goose and the engine
    path -- the CofC ruled this severable from the canary caps, because the
    engine fallback is what actually writes files today."""
    s = str(raw or "").strip()
    for ch in _WIN_FORBIDDEN:
        s = s.replace(ch, "_")
    s = re.sub(r"[^0-9A-Za-z_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_").lower()
    return s


def _soa_service_name(directive_id):
    """services/staged/<name> from a directive_id, e.g.
    build_risk_tier_dashboard_view_contract -> risk_tier_dashboard_view."""
    s = _sanitise_service_name(directive_id)
    for pre in ("build_", "scaffold_", "create_", "emit_"):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    for suf in ("_service_toml", "_service_dir", "_contract", "_router",
                "_logic", "_init", "_api_logic"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return s or _sanitise_service_name(directive_id) or "unnamed_service"


_SCHEMA_HEADER = (
    "REAL SCHEMA -- authoritative, read from the live app.models KL. Use ONLY "
    "these columns; a column not listed here DOES NOT EXIST, and inventing one "
    "is the single most common reason a build is rejected. Access this data "
    "ONLY through the SQLAlchemy ORM (app.models classes + select()/session). "
    "NEVER write raw SQL -- no execute() with an INSERT/SELECT string, no hand-"
    "counted 'VALUES (?, ?, ...)' list: a placeholder/column mismatch there is "
    "the top cause of a RED self-test. To seed a row in a self-test, construct "
    "the ORM object -- session.add(ModelName(col=value, ...)) -- setting every "
    "[required] column. Per-column flags: [PK]=primary key, [required]=NOT NULL "
    "with no default (the ORM constructor MUST set it), [default]=DB fills it if "
    "omitted; unflagged=nullable:\n")


def _fmt_cols(meta):
    """Columns + constraint flags from the KL (column_meta / required_construct),
    so a generated self-test seeds every required (NOT NULL, no-default) column
    and respects PK/defaults -- the fix for 'NOT NULL constraint failed' seed
    gaps. Falls back to the bare column name when no metadata is present (R6: a
    column with unknown constraints is emitted as its name, never dropped)."""
    cols = [c for c in (meta.get("columns") or []) if c]
    cmeta = meta.get("column_meta") or {}
    required = set(meta.get("required_construct") or [])
    parts = []
    for c in cols:
        m = cmeta.get(c) or {}
        flags = []
        if m.get("primary_key"):
            flags.append("PK")
        if c in required:
            flags.append("required")
        elif m.get("nullable") is False:
            flags.append("NOT NULL")
        if m.get("has_default"):
            flags.append("default")
        parts.append("%s [%s]" % (c, ",".join(flags)) if flags else c)
    return ", ".join(parts)


def _soa_schema_excerpt(directive):
    """LIVE schema excerpt for the tables a directive names, from the same
    app.models KL the schema-PRM gate lints against.

    Returns "" when no real table resolves. The CofC ruled that passing
    json.dumps(directive) raw as `service_spec` is NOT acceptable: it hands
    invented columns the authority of a spec and reproduces the observed
    failure (generated code used McpServerRegistry.id when the PK is server_id
    and no `id` column exists -- 241 schema-prm BLOCKs on 2026-08-04 alone). It
    also confounds the canary, because goose-wrong and spec-wrong become
    unattributable. No resolvable table => canary-INELIGIBLE => engine.
    """
    try:
        import schema_kl
        try:
            kl = schema_kl.build_schema_kl()
        except Exception:
            kl = schema_kl.load_schema_kl(
                str(PROJECT_DIR / "graphify-out" / "schema_kl.json"))
    except Exception:
        return ""
    models = ((kl or {}).get("models") or {})
    if not models:
        return ""
    try:
        blob = json.dumps(directive, default=str).lower()
    except Exception:
        blob = str(directive).lower()
    hits = []
    for name, meta in models.items():
        meta = meta or {}
        table = str(meta.get("table") or "")
        if name.lower() in blob or (table and table.lower() in blob):
            cols = [c for c in (meta.get("columns") or []) if c]
            if not cols:
                continue
            hits.append("  %s (table %s): %s" % (name, table, _fmt_cols(meta)))
    if not hits:
        return ""
    return _SCHEMA_HEADER + "\n".join(sorted(hits)[:8])


_SCHEMA_KL_MEMO = {"mtime": None, "kl": None}


def _schema_kl_cached():
    """The schema KL, memoised on the KL file's mtime (CofC 2026-08-11 cond.3).

    _soa_schema_excerpt used to rebuild the KL on every call. That was priced
    for the SOA canary's 8 builds/day; the engine path runs ~588/day, so an
    uncached rebuild would sit on the daemon's hot path 70x more often. Keyed
    on mtime, not on a TTL, so a regenerated KL is picked up immediately and an
    unchanged one is never rebuilt.

    Returns (kl, klass) with klass in {"ok", "kl_error"} -- NEVER a bare None,
    because "the KL is broken" and "the KL says nothing" are different facts
    and this fleet has repeatedly been bitten by collapsing them (R6).
    """
    import schema_kl as _skl
    path = PROJECT_DIR / "graphify-out" / "schema_kl.json"
    try:
        mtime = path.stat().st_mtime
    except Exception:
        mtime = None
    if _SCHEMA_KL_MEMO["kl"] is not None and _SCHEMA_KL_MEMO["mtime"] == mtime:
        return _SCHEMA_KL_MEMO["kl"], "ok"
    try:
        kl = _skl.build_schema_kl()
    except Exception:
        kl = _skl.load_schema_kl(str(path))
    _SCHEMA_KL_MEMO["mtime"] = mtime
    _SCHEMA_KL_MEMO["kl"] = kl
    return kl, "ok"


def _schema_ground_context(directive):
    """THREE-STATE schema grounding: (text, klass) with klass one of
    "matched" / "no_table_matched" / "kl_error".

    WHY THIS EXISTS (CofC 2026-08-11, chairman review). _soa_schema_excerpt
    inlines the real column list and has done since the SOA canary shipped --
    but it was reachable ONLY from _soa_service_spec, i.e. only from the goose
    canary path, which is capped at 8 builds/day. The engine path, which wrote
    588 files on 2026-08-11 alone, never saw it. Its prompt instead told the
    model "SYMBOL TABLE: docs/SCHEMA_TRUTH.md ... read it before writing an
    import" -- a pointer a single-shot chat completion with no filesystem
    CANNOT follow. So the cure existed, was correct, and was wired to the path
    that does not run. schema-PRM blocked 775 builds on 2026-08-10 and 454 by
    12:05 on 2026-08-11 (basis: /home/workspace/logs/goose_runner.log, which
    ROTATED and begins 2026-08-09T20:15 -- no trend may be claimed from it, and
    the "241 blocks on 08-04" comment below is UNKNOWN, not a baseline).

    Returning a CLASS rather than "" is condition 4 of the ruling: an empty
    string that means "no real table in this directive" and an empty string
    that means "the KL failed to load" are indistinguishable to every caller,
    and a change that ships as a silent no-op is the failure mode here.
    """
    try:
        kl, _ = _schema_kl_cached()
    except Exception:
        return "", "kl_error"
    models = ((kl or {}).get("models") or {})
    if not models:
        return "", "kl_error"
    try:
        blob = json.dumps(directive, default=str).lower()
    except Exception:
        blob = str(directive).lower()
    hits = []
    for name, meta in models.items():
        meta = meta or {}
        table = str(meta.get("table") or "")
        if name.lower() in blob or (table and table.lower() in blob):
            cols = [c for c in (meta.get("columns") or []) if c]
            if not cols:
                continue
            hits.append("  %s (table %s): %s" % (name, table, _fmt_cols(meta)))
    if not hits:
        return "", "no_table_matched"
    return (_SCHEMA_HEADER + "\n".join(sorted(hits)[:8])), "matched"


# The engine path is a single chat completion with no filesystem. Telling it to
# go read a file is worse than saying nothing: it names an authority the model
# cannot consult, and a model sent to fetch truth it cannot reach invents it.
# Dropped from the engine prompt whenever the REAL SCHEMA block is present.
_SYMBOL_TABLE_POINTER = re.compile(
    r"SYMBOL TABLE: docs/SCHEMA_TRUTH\.md.*?never invent one\. ", re.S)


_BUS_HEADER = (
    "REAL BUS TABLES -- the COMPLETE list of tables on the write-service bus "
    "(127.0.0.1:8772), read from schema/bus_catalog.json. If your SQL is posted "
    "to :8772 then the table you name MUST be one of these. A name that is not "
    "on this list DOES NOT EXIST -- and that is true even if a .py module in "
    "this repo carries that name. A MODULE IS NOT A TABLE:\n")


# Cached: this is on the per-emission path and the catalog moves once a day.
# (timestamp, rendered_block). Empty tuple means "not looked up yet".
_BUS_TABLES_CACHE = ()
_BUS_TABLES_TTL_S = 3600


def _bus_table_context():
    """The real bus table names, or "" if the snapshot cannot be read.

    WHY THIS EXISTS SEPARATELY FROM _schema_ground_context
      That function grounds against app.models -- the SQLAlchemy plane, 14
      tables. The write-service bus is a DIFFERENT plane with 45, and it was
      NOT in the engine prompt at all. Worse, _engine_task returned the
      UNGROUNDED task text whenever no app.models class matched, which is the
      normal case for a bus-only emission. So the exact directives most likely
      to write bus SQL were the ones that received no table names whatsoever.

      That is where every phantom on the #4080 list came from. Four of them --
      mcp_servers, signal_scores, mcp_tool_definitions, mcp_tool_schemas --
      are one edit distance from a real bus table. Two more, known_threats and
      approval_workflow, are the names of .py MODULES in this repo (see
      BUILDER_ANTIPATTERNS.md AP-005), which is why the header above says so in
      those words.

      45 names is about 500 characters. The cheapest possible grounding for the
      largest observed class of reference failure.

    THREE-STATE IS PRESERVED. This returns a string or "", and it does NOT
    participate in the matched / no_table_matched / kl_error classification of
    _schema_ground_context. That class means "did an app.models table match
    this directive" and it still means exactly that; collapsing a second signal
    into it is how a change ships as a silent no-op.
    """
    global _BUS_TABLES_CACHE
    now = time.time()
    if _BUS_TABLES_CACHE and now - _BUS_TABLES_CACHE[0] < _BUS_TABLES_TTL_S:
        return _BUS_TABLES_CACHE[1]

    # READ THE SNAPSHOT AS TRACKED ON origin/main, NOT FROM THE WORKING TREE.
    #
    # PROJECT_DIR is the BUILD WORKSPACE. It runs behind main -- 163 commits at
    # the time of writing -- and it does not contain schema/bus_catalog.json at
    # all. A plain read of PROJECT_DIR/schema/bus_catalog.json therefore returns
    # nothing, on the exact machine this code runs on, and this whole grounding
    # block would have shipped as a silent no-op.
    #
    # That is audit finding B2 and it is the same trap the 2026-08-11 grounding
    # fix fell into: a cure that is correct and wired to a path that does not
    # run. tools/bus_catalog_guard.sh already learned this and reads
    # `origin/main:schema/bus_catalog.json`; so does this.
    raw = ""
    try:
        r = subprocess.run(
            ["git", "-C", str(PROJECT_DIR), "show",
             "origin/main:schema/bus_catalog.json"],
            capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            raw = r.stdout
    except Exception:                                      # noqa: BLE001
        raw = ""
    if not raw:
        # Fallback for a checkout that IS current -- CI, or a clean worktree of
        # main. Never the primary path on the host.
        try:
            raw = (PROJECT_DIR / "schema" / "bus_catalog.json").read_text(
                encoding="utf-8")
        except Exception:                                  # noqa: BLE001
            raw = ""
    try:
        names = sorted(t for t in (json.loads(raw).get("tables") or {}) if t)
    except Exception:                                      # noqa: BLE001
        names = []
    out = (_BUS_HEADER + "  " + ", ".join(names)) if names else ""
    _BUS_TABLES_CACHE = (now, out)
    return out


def _reads_context(directive):
    """Inline the directive's declared `reads` files into the ENGINE prompt.

    The engine fallback is a single-shot completion with NO filesystem. A
    service_dir_from_exemplar directive's goose/canary path mirrors
    services/_exemplar via its `reads`; the engine gets NONE of that unless it is
    inlined here. Root cause of the 2026-10-29 service-chain logic-build stall:
    the engine already had schema-COLUMN parity with the canary (via
    _schema_ground_context) but NOT exemplar-PATTERN parity -- it knew the real
    columns but not the ORM idiom, so it improvised a raw SQL INSERT with a
    placeholder-count bug and the self-test went RED. Inlining the exemplar
    anchors the cheap engine model to the ORM idiom (no premium model needed).
    Bounded (8KB/file) + fail-soft; empty string when the directive has no reads.
    """
    reads = directive.get("reads") or []
    if not isinstance(reads, (list, tuple)):
        return ""
    blocks = []
    for rel in reads:
        rel = str(rel)
        try:
            p = PROJECT_DIR / rel
            if not p.is_file():
                continue
            body = p.read_text(encoding="utf-8")[:8000]
        except Exception:                                   # noqa: BLE001
            continue
        if body.strip():
            blocks.append(
                "REFERENCE FILE -- mirror this structure and idioms (imports, the "
                "ORM query/write style over app.models, docstring shape); ADAPT to "
                "THIS service, do not copy verbatim, and do NOT write raw SQL:\n"
                "# %s\n%s" % (rel, body))
    return "\n\n".join(blocks)


def _engine_task(task_text, directive, log=None):
    """The engine's task text = the shared _task plus inlined REAL SCHEMA and the
    directive's exemplar `reads` (pattern parity with the goose canary path).

    Scoped to the ENGINE call only (ruling condition 1): the goose path already
    receives the excerpt via _soa_service_spec, and goose is the orchestrator --
    its prompt is the one surface with no cheap rollback.
    """
    text, klass = _schema_ground_context(directive)
    # The bus table list is appended INDEPENDENTLY of the app.models class.
    # A directive that matches no app.models class is precisely the one most
    # likely to write bus SQL, and it used to receive no table names at all.
    bus = _bus_table_context()
    # Exemplar PATTERN parity: the canary mirrors services/_exemplar via `reads`;
    # the engine (single-shot, no filesystem) must have those files INLINED or it
    # has no idiom to copy. See _reads_context.
    ref = _reads_context(directive)
    if ref and log:
        log("[exemplar-inline] %s: inlined %d reference file(s) into engine prompt"
            % (directive.get("directive_id") or directive.get("task") or "?",
               len(directive.get("reads") or [])))
    if klass != "matched":
        if log:
            log("[schema-ground] %s: %s -- engine prompt UNGROUNDED%s"
                % (directive.get("directive_id") or directive.get("task") or "?",
                   klass,
                   " (bus table list still attached)" if bus else ""))
        parts = [task_text] + ([bus] if bus else []) + ([ref] if ref else [])
        return "\n\n".join(parts)
    grounded = _SYMBOL_TABLE_POINTER.sub("", task_text)
    if log:
        log("[schema-ground] %s: matched, %d model(s), %d chars -> engine"
            % (directive.get("directive_id") or directive.get("task") or "?",
               len([ln for ln in text.splitlines() if ln.startswith("  ")]),
               len(text)))
    parts = [grounded, text] + ([bus] if bus else []) + ([ref] if ref else [])
    return "\n\n".join(parts)


def _soa_service_spec(directive, content):
    """Build the `service_spec` param, or None if the directive is ineligible."""
    excerpt = _soa_schema_excerpt(directive)
    if not excerpt:
        return None
    try:
        intent = json.dumps(content, default=str)
    except Exception:
        intent = str(content)
    return (excerpt
            + "\n\nUNVERIFIED INTENT -- the directive as written. It may name tables or "
              "columns that do not exist. Where it disagrees with REAL SCHEMA above, "
              "REAL SCHEMA wins:\n" + intent)


def _soa_day_key():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _soa_state_read():
    try:
        return json.loads(_SOA_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _soa_state_write(state):
    try:
        _SOA_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SOA_STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True),
                                   encoding="utf-8")
    except Exception:
        pass


def recipe_counter(recipe_name, outcome):
    """Per-RECIPE attempts/successes/failures, persisted per UTC day.

    Doctrine R3: a bucket that went to zero must prove the check RAN. An
    ATTEMPTS denominator per recipe is exactly what was missing -- it is what
    distinguishes "this recipe never succeeds" from "this recipe was never
    exercised", and its absence hid a 1082/1082 failure for two days behind an
    aggregate that looked healthy. This EMITS; it blocks nothing (the doctrine
    is explicit that adding another gate is what produced the losses).
    """
    day = _soa_day_key()
    state = _soa_state_read()
    per_day = state.setdefault("per_recipe", {}).setdefault(day, {})
    row = per_day.setdefault(str(recipe_name), {"attempt": 0, "success": 0,
                                                "fail": 0, "capped": 0,
                                                "ineligible": 0})
    if outcome in row:
        row[outcome] += 1
    _soa_state_write(state)
    if outcome in ("success", "fail"):
        log("[recipe-counter] %s %s: attempt=%d success=%d fail=%d "
            "(capped=%d ineligible=%d) day=%s"
            % (recipe_name, outcome, row["attempt"], row["success"], row["fail"],
               row["capped"], row["ineligible"], day))
    return row


def _soa_budget_left():
    """Remaining canary invocations for this UTC day."""
    day = _soa_day_key()
    used = ((_soa_state_read().get("per_recipe") or {}).get(day, {})
            .get(SOA_RECIPE, {}).get("attempt", 0))
    return SOA_GOOSE_MAX_PER_DAY - int(used or 0)


def soa_params_or_none(directive_id, directive, content):
    """Return the extra --params for the SOA recipe, or None to fall through to
    the engine. Reasons to fall through are LOGGED, never silent."""
    if SOA_GOOSE_MAX_PER_DAY <= 0:
        log("[soa-cap] %s: canary disabled (SOA_GOOSE_MAX_PER_DAY=0) -> engine"
            % directive_id)
        return None
    left = _soa_budget_left()
    if left <= 0:
        recipe_counter(SOA_RECIPE, "capped")
        log("[soa-cap] %s: daily canary budget spent (%d/%d) -> engine"
            % (directive_id, SOA_GOOSE_MAX_PER_DAY, SOA_GOOSE_MAX_PER_DAY))
        return None
    spec = _soa_service_spec(directive, content)
    if not spec:
        recipe_counter(SOA_RECIPE, "ineligible")
        log("[soa-cap] %s: no real table resolved from the schema KL -> "
            "canary-ineligible -> engine" % directive_id)
        return None
    name = _soa_service_name(directive_id)
    log("[soa-canary] %s: service_name=%s spec_bytes=%d budget_left=%d"
        % (directive_id, name, len(spec), left))
    return {"service_name": name, "service_spec": spec}


def recipe_required_params(recipe_path):
    """Parse the recipe's OWN `parameters:` block for `requirement: required`.

    Reading the recipe is what makes this general rather than another hardcoded
    list: the defect was a hardcoded param list that silently disagreed with the
    contract the recipe itself declares. Any future recipe now gets its declared
    params, or is reported as unsatisfiable instead of failing 1082 times.
    """
    required = []
    try:
        txt = Path(recipe_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return required
    in_params = False
    cur = None
    for line in txt.splitlines():
        if re.match(r"^parameters:\s*$", line):
            in_params = True
            continue
        if in_params and line[:1] not in ("", " ", "\t", "#"):
            break
        if not in_params:
            continue
        m = re.match(r"\s*-\s*key:\s*(\S+)", line)
        if m:
            cur = m.group(1).strip().strip("\"'")
            continue
        m = re.match(r"\s*requirement:\s*(\S+)", line)
        if m and cur and m.group(1).strip().strip("\"'") == "required":
            required.append(cur)
    return required


def run_goose_task(directive_id, content, extra_env=None, recipe=None, directive_obj=None):
    """Execute Goose on task file with timeout.

    extra_env (from build_routing.build_env_for) routes the architect
    (GOOSE_MODEL) + codegen (ZO_BUILD_TIER) up the ladder by complexity and
    carries task/phase for the build_artifact row builder_mcp emits."""
    log(f"Executing Goose for directive: {directive_id}")

    try:
        _recipe_name = recipe if recipe else "architect"
        recipe_path = PROJECT_DIR / "goose_recipes" / f"{_recipe_name}.yaml"
        if not recipe_path.exists():
            log(f"[recipe] {_recipe_name}.yaml missing -> architect.yaml")
            recipe_path = PROJECT_DIR / "goose_recipes" / "architect.yaml"
        if recipe:
            log(f"[recipe] {directive_id} -> {recipe_path.name}")
        task_desc = json.dumps(content)
        env = {**os.environ, **(extra_env or {})}
        # Shadow-corpus (flag-gated, default OFF): ride directive_id on the model id
        # goose sends to the shim ("<tier>#<id>") so harvest can key-join prompt->build.
        # The shim strips the suffix before routing. Gated behind ZO_SHADOW_DID_IN_MODEL
        # until a live smoke-test confirms goose forwards a '#'-suffixed GOOSE_MODEL
        # verbatim (goose, unlike our own fallback POST, builds the request body itself).
        if os.environ.get("ZO_SHADOW_DID_IN_MODEL") == "1":
            _gm = env.get("GOOSE_MODEL")
            if _gm and directive_id and "#" not in _gm:
                env["GOOSE_MODEL"] = f"{_gm}#{directive_id}"
        if GOOSE_BIN != "goose":
            # FU-017: isolated store for a versioned goose (see GOOSE_BIN note).
            _iso = os.environ.get("ZO_BUILDER_GOOSE_HOME", "/home/workspace/.goose_builder")
            try:
                os.makedirs(_iso, exist_ok=True)
            except Exception:
                pass
            env["HOME"] = _iso
            env["XDG_CONFIG_HOME"] = f"{_iso}/.config"
            env["XDG_DATA_HOME"] = f"{_iso}/.local/share"
            env["XDG_STATE_HOME"] = f"{_iso}/.local/state"
        if extra_env:
            log(f"[LADDER] {directive_id} -> {extra_env.get('GOOSE_MODEL')}")
        # CofC 2026-08-04 / FU-246: supply the params the recipe DECLARES, not a
        # hardcoded list. `service_dir_from_exemplar` requires service_name +
        # service_spec and got neither -> 1082/1082 exit-1 failures. A recipe
        # whose required params we cannot satisfy now falls to the engine with a
        # reason, instead of burning an invocation on a certain failure.
        _params = {"task_description": task_desc}
        _required = recipe_required_params(recipe_path)
        if SOA_RECIPE in recipe_path.name:
            _soa = soa_params_or_none(directive_id, directive_obj, content)
            if _soa is None:
                return {"success": False, "directive_id": directive_id,
                        "error": "soa canary not applicable -- engine fallback"}
            _params.update(_soa)
        _missing = [k for k in _required if k not in _params]
        if _missing:
            log(f"[recipe] {recipe_path.name}: cannot satisfy required params "
                f"{_missing} -> engine fallback")
            recipe_counter(recipe_path.stem, "ineligible")
            return {"success": False, "directive_id": directive_id,
                    "error": f"recipe requires unsatisfiable params {_missing}"}
        _argv = [GOOSE_BIN, "run", "--recipe", str(recipe_path)]
        for _k in (_required or ["task_description"]):
            _argv += ["--params", f"{_k}={_params[_k]}"]
        recipe_counter(recipe_path.stem, "attempt")
        # GH #3415 prevention fix 4: snapshot tracked dirt BEFORE the agent runs
        # so any tracked file that BECOMES dirty during the bracket is
        # attributable to THIS directive (the runner is serial). None = git
        # could not answer = no attribution basis, sweep no-ops (R6).
        _dirty0 = undeclared_write_guard.tracked_dirty(PROJECT_DIR)
        _decl_rel = None
        try:
            if directive_obj:
                _dp = declared_output(directive_obj)
                if _dp:
                    _decl_rel = str(Path(_dp).resolve().relative_to(PROJECT_DIR.resolve()))
        except Exception:
            _decl_rel = None
        _acquired = _SOA_SEM.acquire(timeout=GOOSE_TIMEOUT) if _SOA_SEM else True
        try:
            result = subprocess.run(
                _argv,
                capture_output=True,
                text=True,
                timeout=GOOSE_TIMEOUT,
                cwd=str(PROJECT_DIR),
                env=env
            )
        finally:
            if _SOA_SEM and _acquired:
                try:
                    _SOA_SEM.release()
                except Exception:
                    pass
            # GH #3415 fix 4: revert tracked writes this run never declared
            # (load-bearing markers always; anything tracked for build-class).
            # Fail-open inside sweep(); forensics under _ghost_writes/ before
            # any restore; verdict/flow untouched -- the declared-output gate
            # still governs success.
            _reverted = undeclared_write_guard.sweep(
                PROJECT_DIR, directive_id, _dirty0,
                declared_relpath=_decl_rel, log=log)
            if _reverted:
                try:
                    record_lesson(LESSONS_DIR, directive_id, directive_id,
                                  "undeclared_write",
                                  f"undeclared tracked write(s) reverted: "
                                  f"{[a['file'] for a in _reverted]} -- write ONLY "
                                  f"the declared output path; never repo package "
                                  f"markers or other tracked files")
                except Exception:
                    pass

        if result.returncode == 0:
            recipe_counter(recipe_path.stem, "success")
            log(f"Goose execution succeeded for {directive_id}")
            return {
                "success": True,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "directive_id": directive_id
            }
        else:
            recipe_counter(recipe_path.stem, "fail")
            log(f"Goose execution failed (exit {result.returncode}): {result.stderr}")
            return {
                "success": False,
                "error": result.stderr or "Unknown error",
                "directive_id": directive_id
            }
            
    except subprocess.TimeoutExpired:
        log(f"Goose timeout exceeded for {directive_id}")
        return {
            "success": False,
            "error": f"Timeout after {GOOSE_TIMEOUT}s",
            "directive_id": directive_id
        }
    except FileNotFoundError:
        log("Goose not installed - using fallback")
        return {
            "success": False,
            "error": "Goose not installed",
            "directive_id": directive_id
        }
    except Exception as e:
        log(f"Goose execution error: {e}")
        return {
            "success": False,
            "error": str(e),
            "directive_id": directive_id
        }
