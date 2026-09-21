#!/usr/bin/env python3
"""
hermetic_manifest.py -- the data the CI smoke ladder gates on.

Everything here is verified to work on a stock GitHub runner (Python 3.11 +
duckdb/fastapi/requests) with NO ZoComputer host, NO write_service, and NO
/home/workspace layout. Keep it that way: only add a module to
IMPORTABLE_MODULES after confirming it imports clean in the CI venv, and only
add a schema file after confirming the loader builds it into an in-memory
DuckDB.

This is a deliberately small, high-signal allowlist rather than a
"import everything" sweep: most of the 780 modules are host daemons whose
import has side effects (network calls, /home/workspace reads). The allowlist
grows as modules are made portable -- that growth is itself the health metric.
"""
from __future__ import annotations

from pathlib import Path

# Repo root = two levels up from this file (tests/ci/hermetic_manifest.py).
REPO_ROOT = Path(__file__).resolve().parents[2]


# --- Tier 1: modules that MUST import cleanly in CI -------------------------
# Verified 2026-05-29 against the CI venv. These are the portable core: the
# schemas-as-code package, the GH evaluator/fetcher, the promoter, and the
# pure scoring/taxonomy leaves with no host coupling.
IMPORTABLE_MODULES = [
    "signal_weights",
    "verdict_taxonomy",
    "zo_sentinel.schemas.loader",
    "zo_sentinel.probes.duckdb_schema_uptime_probe",
    "zo_sentinel.evaluators.gh_actions_fetcher",
    "zo_sentinel.promoters.proposed_to_pending_promoter",
    # skip=>retire queue janitor (stdlib + pure build_completion/build_lessons).
    "zo_sentinel.queue_janitor",
    # grounded deterministic builder engine (requests + pure helpers).
    "zo_sentinel.engine_build",
    # self-refilling anchor (stdlib-only KL candidate miner).
    "zo_sentinel.anchor_refill",
    # declarative operational policy layer (stdlib; tomllib-guarded).
    "zo_sentinel.policy",
    # managed GPU-job lifecycle (stdlib; vast SDK injected, never imported here).
    "zo_sentinel.vast_jobs",
    # vuln-intel identity helpers (stdlib, pure).
    "vuln_identity",
    # SFT training-job ingestion (host-free, stdlib-only by design).
    "zo_sentinel.sft.schema",
    "zo_sentinel.sft.ingest",
    "zo_sentinel.sft.batch_runner",
    # Gate-8 breaker state (fcntl-optional; env-repointable state file).
    "gate_quality_state",
    # Net-new code-artifact ingestor (host-free; storage seam is mockable).
    "zo_sentinel.ingestor.model",
    "zo_sentinel.ingestor.contracts",
    "zo_sentinel.ingestor.store",
    "zo_sentinel.ingestor.ingestor",
    "zo_sentinel.ingestor.governor",
    # goose -> GitHub-PR bridge (host-free; git/gh seam is mockable).
    "zo_sentinel.publisher.gitops",
    "zo_sentinel.publisher.publisher",
    # goose build-path glue: complexity->ladder routing + build_artifact schema.
    "zo_sentinel.build_routing",
    # MERGE_AUDIT_2026-08-23 G1: the ASGI entrypoint. tier4_spine used to skip
    # its own `import app.main` on failure with the note "owned by tier1" -- but
    # no module under the `app` package was ever on this list, so nothing owned
    # it and neither tier tested it. It is owned HERE now, explicitly. Importing
    # it also pulls the whole generated spine (28 mounts on 2026-08-25), which
    # is what puts the previously ungated app/ modules under a real assertion.
    # Portable by the same standard as the rest of this list: no host, no
    # write_service, no /home/workspace -- include_spine records mount failures
    # on app.state rather than raising, and tier4 is what judges them.
    "app.main",
]


# --- App-foundation modules (3-tier SaaS app surface) ----------------------
# Imported by the smoke ladder ONLY IF the file exists (present_app_modules
# filters by existence), so each is import-gated as the autonomous builder lands
# it -- without breaking the gate while a module is not yet built. Same
# allowlist-grows-as-portable health-metric pattern as IMPORTABLE_MODULES.
APP_FOUNDATION_MODULES = [
    "tenant_org_model", "oauth_login_service", "rbac_enforcer",
    "verdict_breakdown_api", "org_entity_search_api", "overview_dashboard_api",
    "entity_report_exporter", "verdict_watchlist_service", "org_api_key_manager",
    "product_audit_log",
]


def present_app_modules() -> list:
    """APP_FOUNDATION_MODULES whose <name>.py the autonomous builder has actually
    landed at the repo root -- import-gated as they appear; absent ones are skipped."""
    return [m for m in APP_FOUNDATION_MODULES if (REPO_ROOT / f"{m}.py").exists()]

# Known NOT-portable (documented so nobody re-adds them by mistake):
#   zo_sentinel.mcp_servers.directive_mcp -> needs the `mcp` SDK (not a CI dep)
NON_PORTABLE_NOTES = {
    "zo_sentinel.mcp_servers.directive_mcp": "requires `mcp` SDK; not installed in CI",
}


# --- Tier 2: committed schema snapshots the loader must build ----------------
# Paths are repo-relative. The loader (zo_sentinel.schemas.loader) reads these
# and the schema files live under schemas/. Tier 2 asserts each builds into a
# fresh in-memory DuckDB and that compute_schema_hash is deterministic.
SCHEMA_FILES = [
    "schemas/duckdb_schema.json",
    "schemas/mesh_memory_schema.json",
]


# --- Tier 3: mock write_service contract ------------------------------------
# The round-trip table the service-contract tier writes to / reads back.
MOCK_CONTRACT_TABLE = "ci_smoke_roundtrip"


def _read_quarantine(filename: str) -> set[str]:
    """Parse a quarantine list file (one repo-relative path per line, '#'
    comments + trailing '  # reason' stripped). Missing file -> empty set."""
    qfile = Path(__file__).resolve().parent / filename
    out: set[str] = set()
    if not qfile.exists():
        return out
    for line in qfile.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        path = line.split("#", 1)[0].strip()
        if path:
            out.add(path.replace("\\", "/"))
    return out


def quarantined_syntax_files() -> set[str]:
    """Repo-relative paths Tier 0 must NOT gate on (known pre-existing breaks)."""
    return _read_quarantine("syntax_quarantine.txt")


def quarantined_html_files() -> set[str]:
    """Repo-relative HTML paths FE0 must NOT gate on (known placeholder stubs)."""
    return _read_quarantine("html_quarantine.txt")
