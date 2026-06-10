#!/usr/bin/env python3
"""scan_capmap.py -- DETERMINISTIC capability-map scanner.

Replaces the one-shot LLM analysis pass that produced capmap.json with a static
scan, so the knowledge-layer loop (capmap -> build_app_graph -> graph_gap_directives)
runs UNATTENDED and REPRODUCIBLY. Emits the same JSON schema the agent did.

Truth sources (all read fresh each run):
  schema/app.sql   -> canonical tables (CANON) for drift detection
  *.py (root)      -> endpoints (route decorators via AST) + table I/O
                      (SQL FROM/INTO/UPDATE/JOIN + ws_write("table", ...))
  *.html (root)    -> UI files
  app_routes.UI_FILES + .py FileResponse/HTMLResponse/StaticFiles -> served vs orphaned

Areas are a stable domain config (tables + path-prefixes + ui files); the LIVE
state (which endpoints exist, reach storage, UIs served, drift) is computed, not
hardcoded -- so the map tracks the code.

    python tools/scan_capmap.py [out.json]      # default: capmap.json
"""
import ast, json, os, re, sys, glob, warnings

warnings.filterwarnings("ignore", category=SyntaxWarning)  # repo files w/ bad escapes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "capmap.json")
sys.path.insert(0, ROOT)

AREAS = [
    {"name": "Registry/Assessment",
     "tables": ["mcp_server_registry", "mcp_signal_scores", "mcp_threat_associations",
                "mcp_fingerprints", "mcp_tool_hashes"],
     "prefixes": ["/api/servers", "/v1/registry", "/v1/assess", "/v1/threats", "/v1/bulk", "/v1/export"],
     "ui": ["sentinel_status.html", "mcp_detail_view.html", "mcp_detail_view_ui.html",
            "mcp_detail_view_ui_v2.html"]},
    {"name": "Submissions", "tables": ["mcp_submissions"],
     "prefixes": ["/api/submissions", "/api/submit", "/api/submission"],
     "ui": ["mcp_submission_portal.html", "admin_submissions.html"]},
    {"name": "Decisions", "tables": ["mcp_decisions"], "prefixes": ["/api/decision"], "ui": []},
    {"name": "Policies", "tables": ["mcp_policy_rules"], "prefixes": ["/api/policies"],
     "ui": ["admin_policies.html"]},
    {"name": "Attestations", "tables": ["mcp_attestations"],
     "prefixes": ["/api/attestations"], "ui": ["admin_attestations.html"]},
    {"name": "Exemptions", "tables": ["mcp_exemptions"], "prefixes": ["/api/exemptions"],
     "ui": ["admin_exemptions.html"]},
    {"name": "Risk/Forensics",
     "tables": ["mcp_risk_register", "shodan_results", "github_velocity", "npm_typosquat_alerts"],
     "prefixes": ["/risks", "/forensic", "/servers"], "ui": []},
    {"name": "Audit", "tables": ["audit_log"], "prefixes": ["/api/audit-log", "/api/audit", "/audit"], "ui": []},
    {"name": "Auth", "tables": ["auth_tokens"], "prefixes": ["/api/auth"], "ui": []},
    {"name": "Dashboard/Summary", "tables": [],
     "prefixes": ["/api/dashboard", "/dashboard"],
     "ui": ["dashboard.html", "builder_eye_dashboard.html", "builder_eye_dashboard_public.html",
            "builder_eye_dashboard_hr.html"]},
    {"name": "Pathway/Funnel", "tables": ["mcp_discovery_candidates", "mcp_signal_enrichments"],
     "prefixes": ["/api/pathway"], "ui": ["pathway_to_20k.html"]},
    {"name": "Manual Override", "tables": [], "prefixes": ["/override"], "ui": []},
]
TABLE_AREA = {t: a["name"] for a in AREAS for t in a["tables"]}
UI_AREA = {u: a["name"] for a in AREAS for u in a["ui"]}

SQL_TBL = re.compile(r'\b(?:from|into|update|join)\s+([a-z_][a-z0-9_]*)', re.I)
WS_WRITE = re.compile(r'ws_write\(\s*["\']([a-z_][a-z0-9_]*)["\']')
WRITE_KW = re.compile(r'\b(insert|update\s|ws_write)\b', re.I)
SQL_NOISE = {"select", "where", "set", "values", "by", "information_schema", "duckdb_constraints"}
HTML_REF = re.compile(r'["\']([A-Za-z0-9_]+\.html)["\']')


def canon_tables():
    p = os.path.join(ROOT, "schema", "app.sql")
    if not os.path.isfile(p):
        return set()
    return set(re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', open(p, encoding="utf-8").read()))


def all_tables():
    """Every REAL table name (any CREATE TABLE in the repo). Used to filter the
    FROM/JOIN regex so it can't mistake English words ('the', 'server', 'existing'
    after 'from'/'update' in comments) for tables."""
    u = set()
    for f in glob.glob(os.path.join(ROOT, "*.py")):
        try:
            u |= set(re.findall(r'CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)',
                                open(f, encoding="utf-8", errors="replace").read()))
        except Exception:
            pass
    return {t.lower() for t in u}


def route_decorator(dec):
    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
        attr = dec.func.attr.lower()
        base = dec.func.value
        if attr in ("get", "post", "put", "delete", "patch") and isinstance(base, ast.Name) \
                and base.id in ("app", "router"):
            if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                return attr.upper(), dec.args[0].value
    return None


def extract_tables(body, universe):
    ws = {t.lower() for t in WS_WRITE.findall(body)}          # ws_write("t") -> explicit table
    sql = {t.lower() for t in SQL_TBL.findall(body) if t.lower() in universe}  # filter word-noise
    return sorted((ws | sql) - SQL_NOISE), bool(WRITE_KW.search(body))


def scan_endpoints(universe):
    eps = []
    for pyf in sorted(glob.glob(os.path.join(ROOT, "*.py"))):
        try:
            src = open(pyf, encoding="utf-8", errors="replace").read()
            tree = ast.parse(src)
        except Exception:
            continue
        lines = src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                r = route_decorator(dec)
                if not r:
                    continue
                method, path = r
                body = "\n".join(lines[node.lineno - 1: getattr(node, "end_lineno", node.lineno)])
                tables, writes = extract_tables(body, universe)
                eps.append({"method": method, "path": path, "file": os.path.basename(pyf),
                            "tables": tables, "writes": writes})
    return eps


def served_set():
    served = set()
    try:
        import app_routes
        served |= {fn for fn, _ in app_routes.UI_FILES.values()}
    except Exception:
        pass
    for pyf in glob.glob(os.path.join(ROOT, "*.py")):
        src = open(pyf, encoding="utf-8", errors="replace").read()
        if any(k in src for k in ("FileResponse", "HTMLResponse", "StaticFiles")):
            served |= {h for h in HTML_REF.findall(src)}
    return served


def endpoint_area(ep, canon):
    for t in ep["tables"]:
        if t in TABLE_AREA:
            return TABLE_AREA[t]
    best = None
    for a in AREAS:
        for p in a["prefixes"]:
            if ep["path"] == p or ep["path"].startswith(p):
                if best is None or len(p) > best[1]:
                    best = (a["name"], len(p))
    return best[0] if best else "Unassigned"


def main():
    CANON = canon_tables()
    UNIVERSE = {t.lower() for t in CANON} | all_tables()   # all real tables, for noise-filtering
    eps = scan_endpoints(UNIVERSE)
    served = served_set()
    html = [os.path.basename(h) for h in sorted(glob.glob(os.path.join(ROOT, "*.html")))]

    # group endpoints by area
    by_area = {a["name"]: [] for a in AREAS}
    by_area["Unassigned"] = []
    for ep in eps:
        ar = endpoint_area(ep, CANON)
        drift = [t for t in ep["tables"] if t and t not in CANON]
        io = (("writes " if ep["writes"] else "reads ") + ", ".join(ep["tables"])) if ep["tables"] else "no table I/O"
        if drift:
            io += f"  [DRIFT: {','.join(drift)} not in app.sql]"
        by_area.setdefault(ar, []).append(
            {"method": ep["method"], "path": ep["path"], "file": ep["file"],
             "reaches_storage": bool(ep["tables"]), "io": io})

    # group UI by area
    ui_by_area = {a["name"]: [] for a in AREAS}
    ui_by_area["Unassigned"] = []
    for h in html:
        ar = UI_AREA.get(h, "Unassigned")
        is_served = h in served
        kind = "admin" if "admin" in h.lower() else "user"
        ui_by_area.setdefault(ar, []).append(
            {"file": h, "kind": kind, "served": is_served,
             "evidence": "served by a route or the shell UI_FILES" if is_served
                         else "no FileResponse/route and not in shell UI_FILES"})

    areas_out, n_orphan, n_ep = [], 0, 0
    for a in AREAS:
        name = a["name"]
        e = by_area.get(name, [])
        u = ui_by_area.get(name, [])
        n_ep += len(e)
        gaps = []
        for x in u:
            if not x["served"]:
                gaps.append(f"{x['file']} is orphaned (no serving route)")
                n_orphan += 1
        for x in e:
            if "DRIFT" in x["io"]:
                gaps.append(f"{x['method']} {x['path']} touches a table not in app.sql")
            elif not x["reaches_storage"]:
                gaps.append(f"{x['method']} {x['path']} reaches no storage")
        if not e:
            gaps.append("no HTTP endpoint for this area")
        wired = [x for x in e if x["reaches_storage"] and "DRIFT" not in x["io"]]
        served_ui = [x for x in u if x["served"]]
        if not e or not any(x["reaches_storage"] for x in e):
            status = "gap"
        elif wired and (served_ui or not u):
            status = "complete" if not gaps else "partial"
        else:
            status = "partial"
        areas_out.append({"name": name, "tables": a["tables"], "endpoints": e, "ui": u,
                          "gaps": gaps, "status": status})

    summary = {
        "areas_total": len(areas_out),
        "areas_complete": sum(1 for a in areas_out if a["status"] == "complete"),
        "areas_partial": sum(1 for a in areas_out if a["status"] == "partial"),
        "areas_gap": sum(1 for a in areas_out if a["status"] == "gap"),
        "orphaned_ui_count": n_orphan,
        "endpoints_total": n_ep,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "areas": areas_out}, f, indent=2)
    print(f"wrote {OUT}")
    print(f"  {summary['endpoints_total']} endpoints, {len(html)} UIs "
          f"({summary['orphaned_ui_count']} orphaned), "
          f"{summary['areas_complete']}c/{summary['areas_partial']}p/{summary['areas_gap']}g")
    unassigned_e = len(by_area.get("Unassigned", []))
    if unassigned_e:
        print(f"  note: {unassigned_e} endpoints unassigned to a domain area (path didn't match any prefix/table)")


if __name__ == "__main__":
    main()
