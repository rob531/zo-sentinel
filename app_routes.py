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
    # --- relative API namespaces (already same-origin in the HTML) ---
    "/api/pathway": 8790,   # pathway_api      # VERIFY (may be its own port)
    "/api":         8790,   # ui_server (auth, servers, submissions, dashboard, audit, policies, attestations)
    "/v1/bulk":     8784,   # bulk_assess_api  (evidenced: MVP audit)
    "/v1":          8782,   # registry_api     # VERIFY
    "/servers":     8779,   # forensic_detail_api_v2 (evidenced: MVP audit)
    "/dashboard":   8785,   # dashboard_api    # VERIFY
    "/risks":       8786,   # search_api       # VERIFY
    "/audit":       8788,   # registry_api_v2  # VERIFY
    # --- raw-port prefixes (mirror RAW_PORT_PREFIX; the codemod targets these) ---
    "/bus":      8772,
    "/build":    8795,
    "/override": 8776,
    "/attest":   8780,
    "/svc8781":  8781,
    "/svc8773":  8773,
    "/svc8775":  8775,
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
