"""app_routes.py -- the SINGLE SOURCE OF TRUTH for the same-origin shell's
topology. Pure config, NO third-party imports, so both `app_shell.py` and the
`tools/relativize_ui_endpoints.py` codemod import it without side effects.

Two maps:
  ROUTES           prefix -> upstream localhost port  (the shell proxies these)
  RAW_PORT_PREFIX  port   -> same-origin prefix        (the codemod rewrites these)

The browser only ever talks to the shell (same origin). The shell forwards,
server-side, to whatever localhost port each service currently binds.

PORTS MARKED `# VERIFY` are not pinned in the module source (the launcher/go.sh
assigns them); confirm against the box. The shell's /healthz probes every
upstream on boot so a wrong port shows up immediately as unreachable.
"""

# port -> same-origin prefix. These are the absolute http://127.0.0.1:PORT URLs
# the UI currently hardcodes; the codemod rewrites each to its prefix so the
# page works behind ANY origin (localhost, GH runner, cloud).
RAW_PORT_PREFIX = {
    8772: "/bus",       # write_service -- the single DuckDB writer (evidenced: bootstrap + HTML)
    8795: "/build",     # build_watcher_api  (HTML: /api/build-stream, SSE)
    8776: "/override",  # manual_override_api (evidenced: literal port=8776)
    8780: "/attest",    # attestation engine  # VERIFY
    8781: "/svc8781",   # detail writer?      # VERIFY (HTML posts /write here)
    8773: "/svc8773",   # VERIFY
    8775: "/svc8775",   # VERIFY
}

# prefix -> upstream port. Longest-prefix-first wins at match time. Includes the
# raw-port prefixes above PLUS the relative API namespaces the UI already calls
# same-origin (those don't need a codemod, just routing).
ROUTES = {
    # --- RUNNING -- confirmed by on-box /healthz + ss port map (2026-06-10) ---
    "/api/submit":     8780,  # approval_workflow.py (submission intake)
    "/api/decision":   8780,  # approval_workflow.py (analyst decision)
    "/api/submission": 8780,  # approval_workflow.py (GET /api/submission/{id})
    "/api/audit":      8780,  # approval_workflow.py (decision audit)
    "/api":            8790,  # ui_server.py (auth, servers, dashboard, policies, attestations, audit-log)
    "/v1":             8781,  # registry_api.py        (FIXED from 8782)
    "/bus":            8772,  # write_service.py
    "/build":          8795,  # build_watcher_api.py
    "/svc8781":        8781,  # registry_api.py (the UI's REGISTRY_API alias)
    "/svc8773":        8773,  # inference_router_service.py
    "/attest":         8780,  # NOTE: :8780 is approval_workflow; a dedicated attestation engine is NOT running
    # --- DOWN -- service not listening on the box right now -> shell returns 502.
    #     This is a LAUNCH/supervisor gap, not a port error. Ports best-known.
    "/v1/bulk":        8784,  # bulk_assess_api.py        DOWN
    "/servers":        8779,  # forensic_detail_api_v2.py DOWN
    "/override":       8776,  # manual_override_api.py    DOWN
    "/dashboard":      8785,  # dashboard_api.py          DOWN (port unconfirmed)
    "/risks":          8786,  # search_api.py             DOWN (port unconfirmed)
    "/audit":          8788,  # registry_api_v2.py        DOWN (port unconfirmed)
    "/svc8775":        8775,  # DOWN
}

# clean URL -> (html filename, requires_admin). Serving these same-origin is the
# fix for the "orphaned UI" finding: now a route DOES serve them, on the same
# origin as the API, so the hardcoded-localhost break disappears.
UI_FILES = {
    "/":                     ("sentinel_status.html",          False),
    "/submit":               ("mcp_submission_portal.html",    False),
    "/detail":               ("mcp_detail_view_ui_v2.html",    False),
    "/pathway":              ("pathway_to_20k.html",           False),
    "/dash":                 ("builder_eye_dashboard_public.html", False),
    "/admin/submissions":    ("admin_submissions.html",        True),
    "/admin/policies":       ("admin_policies.html",           True),
    "/admin/attestations":   ("admin_attestations.html",       True),
    "/admin/exemptions":     ("admin_exemptions.html",         True),
    "/admin/dashboard":      ("builder_eye_dashboard.html",    True),
}


def prefix_for_path(path: str):
    """Longest-prefix match -> upstream port, or None."""
    best = None
    for pref, port in ROUTES.items():
        if path == pref or path.startswith(pref + "/"):
            if best is None or len(pref) > len(best[0]):
                best = (pref, port)
    return best  # (prefix, port) or None
