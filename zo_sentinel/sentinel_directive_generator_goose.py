#!/usr/bin/env python3
"""
sentinel_directive_generator_goose.py -- Goose-driven directive generator.

PHASE 0b SIBLING DAEMON. Runs ALONGSIDE the existing
sentinel_directive_generator.py without touching it. Different SERVICE_NAME,
different heartbeat key, different output directory.

KEY DESIGN DECISIONS (idempotency / non-breakage):
  1. Writes proposals to directives/proposed/, NEVER to directives/pending/.
     goose_runner.py does not watch proposed/, so this daemon CANNOT
     accidentally feed the active build chain. Promotion is a separate step.
  2. The existing sentinel_directive_generator.py keeps running unchanged.
     If this daemon crashes, the legacy generator still cycles (currently
     producing 0 directives/cycle, but the existing behavior is preserved).
  3. NOT registered in /etc/zo/supervisord-user.conf by this code. Robin
     adds the supervisord block manually when ready. See ROLLOUT.md.
  4. All LLM calls flow through the existing ladder_shim:8796 + Goose
     subprocess pattern. No new model. No new API key. No new shim.
  5. Read-only access to write_service /query for context bundle. Heartbeat
     is best-effort and never raises.

EQUIVALENCE TO EXISTING GENERATOR:
  - Same cycle structure (poll, build context, generate, sleep)
  - Same heartbeat cadence (60s)
  - Same prompt INPUTS (schema, failed_modules, recent_failures,
    registry_summary, layer1)
  - DIFFERENT prompt OUTPUT path: subprocess(goose run --recipe
    directive_architect.yaml) instead of call_minimax(prompt) + JSON-parse
  - DIFFERENT write target: directives/proposed/ instead of directives/pending/
"""
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SERVICE_NAME    = "directive_generator_goose"   # distinct from legacy
SENTINEL_DIR    = Path("/home/workspace/zo_sentinel")
RECIPE_PATH     = SENTINEL_DIR / "goose_recipes" / "directive_architect.yaml"
PROPOSED_DIR    = SENTINEL_DIR / "directives" / "proposed"
PENDING_DIR     = SENTINEL_DIR / "directives" / "pending"
LOG_PATH        = Path("/home/workspace/logs/directive_generator_goose.log")

POLL_SECS       = int(os.environ.get("DGG_POLL_SECS", 600))    # 10 min default
HEARTBEAT_SECS  = int(os.environ.get("DGG_HEARTBEAT_SECS", 60))
GOOSE_TIMEOUT   = int(os.environ.get("DGG_GOOSE_TIMEOUT", 240))   # was 480; a converging cycle is fast, a tool-call LOOP just burns -- fail it sooner
ARCHITECT_MAX_TURNS = int(os.environ.get("DGG_MAX_TURNS", 24))   # CLI --max-turns: hard cap inside goose core loop; canary-proven to bound stdio-MCP bridge-tool loops where recipe settings.max_turns + PreToolUse hook did NOT. 0 = off.
MAX_PROPOSED    = int(os.environ.get("DGG_MAX_PROPOSED_DEPTH", 40))
# STARVATION FLOOR: the builder queue is NEVER empty. See _starvation_floor().
FLOOR_ON        = os.environ.get("DGG_STARVATION_FLOOR", "1") == "1"
FLOOR_SEED_N    = int(os.environ.get("DGG_FLOOR_SEED_N", 3))
# Architect-scoped goose BINARY -- lets the architect run a DIFFERENT goose
# version from the builder (goose_runner.py, which keeps bare `goose` on PATH).
# Default "goose" = today (both roles share the PATH binary). Point this at a
# second install (e.g. /usr/local/bin/goose-1.38) to upgrade ONLY the architect
# -- builder blast-radius stays zero. Reversible: unset -> back to PATH goose.
ARCHITECT_GOOSE_BIN = os.environ.get("ZO_ARCHITECT_GOOSE_BIN", "goose")
IDLE_GATE       = os.environ.get("DGG_IDLE_GATE", "1") == "1"   # batch-when-idle (herd-safe)
IDLE_MIN        = int(os.environ.get("DGG_IDLE_MIN", 8))        # build side quiet >= N min = idle
LAYER1_FIELD_CAP = int(os.environ.get("DGG_LAYER1_FIELD_CAP", 4000))  # per-field char cap on the
                            # layer1 knowledge maps. The FULL maps made the base ctx ~38KB, which at
                            # 52KB total (with the module list) made the slow ladder architect TIME
                            # OUT on ~half its 480s cycles. Capping each of the 4 fields to ~4KB
                            # keeps the highest-signal head, shrinks base ctx to ~16-20KB.
CTX_MODULE_BUDGET = 30000   # byte ceiling for the WHOLE ctx incl. the module list, under the 60000
                            # json.dumps cap. With layer1 capped, base ctx ~20KB; 30000 leaves ~10KB
                            # for ~300 already_built_modules. Total ctx ~30KB -> architect completes
                            # reliably (the prior 38KB cycle finished rc=0 in ~213s; 52KB was flaky).
HEARTBEAT_URL   = "http://127.0.0.1:8772/write"
WS_QUERY_URL    = "http://127.0.0.1:8772/query"

PROPOSED_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [directive_gen_goose] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(SERVICE_NAME)

_stop_requested = threading.Event()


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _heartbeat(status: str = "healthy", note: str = "") -> None:
    """Best-effort POST to write_service. Never raises."""
    try:
        requests.post(
            HEARTBEAT_URL,
            json={
                "table": "service_health",
                "rows": {
                    "service":        SERVICE_NAME,
                    "last_heartbeat": _now_iso(),
                    "status":         status,
                    "meta":           json.dumps({"note": note})[:500] if note else None,
                },
                "wait": True,
            },
            timeout=5,
        )
    except Exception:
        pass


def _heartbeat_loop() -> None:
    while not _stop_requested.is_set():
        _heartbeat("healthy")
        _stop_requested.wait(HEARTBEAT_SECS)


# ---------------------------------------------------------------------------
# Context bundle assembly
# ---------------------------------------------------------------------------

def _count_proposed() -> int:
    if not PROPOSED_DIR.exists():
        return 0
    return sum(
        1 for p in PROPOSED_DIR.glob("*.json")
        if not p.name.endswith(".done.json")
        and not p.name.endswith(".failed.json")
        and ".bak" not in p.name   # FU-011: stale .bak copies must not mask queue emptiness
    )


def _count_pending() -> int:
    if not PENDING_DIR.exists():
        return 0
    # FU-011: stale .bak copies must not mask queue emptiness (starvation floor
    # reads this count -- a .bak-named leftover would keep the floor dormant).
    return sum(1 for p in PENDING_DIR.glob("*.json") if ".bak" not in p.name)


def _queued_stems() -> set:
    """Every task name already in flight or finished -- the dedup set."""
    stems = set()
    for d in (PROPOSED_DIR, PENDING_DIR, PROPOSED_DIR.parent):
        if not d.exists():
            continue
        for p in d.glob("*.json"):
            if ".bak" in p.name:   # FU-011: a stale .bak must not suppress a live re-proposal
                continue
            stems.add(p.stem.replace(".done", "").replace(".failed", ""))
    return stems


# A floor that seeds JUNK is worse than no floor: it manufactures hollow builds
# and churn PRs. Two live fires taught the same lesson twice:
#
#   17:02  foo_bar               <- a PLACEHOLDER in PRODUCT_SPEC.md:149
#          anchor_refill         <- ALREADY EXISTS (zo_sentinel/anchor_refill.py)
#          tier1_inline_enricher <- "enricher": a DEPRECATED class
#   17:18  snake_case            <- another prose artifact, sitting in the gaps
#                                   map right next to settings.py
#
# The first fix denylisted the junk. The second fire walked straight around it.
#
# DENYLISTING JUNK IS WHACK-A-MOLE. The gaps map is mined from PROSE, so it will
# always contain words that merely LOOK like filenames, and you cannot enumerate
# them in advance. So: invert it. A real build target in this codebase has a
# recognisable SHAPE -- it is named for what it DOES. Everything else is rejected
# by DEFAULT, including whatever the prose invents tomorrow.

# What a real module in this repo is called: <domain>_<role>.py
_FLOOR_ROLE_SUFFIXES = (
    "_api", "_service", "_daemon", "_loader", "_resolver", "_ingestor",
    "_ingester", "_consumer", "_monitor", "_linker", "_indexer", "_watcher",
    "_publisher", "_exporter", "_importer", "_collector", "_adapter",
    "_bridge", "_gate", "_guard", "_worker", "_job", "_feed", "_scorer",
    "_refresher", "_validator", "_analyzer", "_analyser", "_reporter",
    "_dashboard", "_view", "_report", "_sync", "_router", "_routers",
    "_rollup", "_summary", "_audit", "_probe", "_runner",
    # FU-040/FU-032 audit 2026-07-20: these role words appear on live
    # `- directive candidate:` lines in PRODUCT_SPEC.md but had no entry here,
    # so chairman-spec'd targets were invisible to the seeding path purely on
    # name shape. score_run_ledger_writer.py (PHASE 8b) was the case that
    # surfaced it. Deliberately restricted to suffixes the spec ACTIVELY asks
    # for: role words that only appear on out-of-scope lines (_builder,
    # _dispatcher, _learner -- graphql_schema_builder / incident_webhook_
    # dispatcher / pattern_learner are all explicitly OUT OF SCOPE) are NOT
    # added, so the floor still cannot seed something the spec forbids.
    "_writer", "_registry", "_planner", "_manager", "_digest",
    "_model", "_log", "_enforcer", "_verifier", "_override",
)

# Task-shaped prefixes (edit/wire work) are also legitimate build targets.
_FLOOR_TASK_PREFIXES = ("wire_", "rewire_", "integrate_", "verify_", "build_")

# DEPRECATED classes: the SFT student model OWNS risk scoring now, so hand-built
# signal/enrichment modules are dead work. The recipe already forbids the
# ARCHITECT from proposing these -- the floor must obey the SAME rule, or it
# becomes a back door around the architect's own guardrails.
_FLOOR_DEPRECATED_SUBSTR = (
    "enrich", "signal_", "_signal", "enumerat", "fingerprint",
    "trust_synthesiser", "signal_analyser",
)


def _existing_anywhere() -> set:
    """Every module basename that EXISTS anywhere in the tree.

    anchor_refill.py lives at zo_sentinel/anchor_refill.py, but
    anchor_refill._disk_names() only scans the SENTINEL_DIR top level -- so the
    floor happily "discovered" a module that has existed for months and proposed
    rebuilding it. Recurse, and skip the noise dirs.
    """
    skip = {".git", "__pycache__", "node_modules", ".venv", "directives",
            "logs", "backups"}
    names = set()
    try:
        for p in SENTINEL_DIR.rglob("*"):
            if p.is_file() and p.suffix in (".py", ".html"):
                if any(part in skip for part in p.parts):
                    continue
                names.add(p.name)
                names.add(p.stem)
    except Exception:
        pass
    return names


def _is_seedworthy(fname: str) -> bool:
    """Does this LOOK like a real build target? Reject by default.

    An ALLOWLIST on shape, not a denylist on junk -- because the gaps map is
    mined from prose and will keep inventing new junk (`foo_bar`, `snake_case`,
    `settings`) that no denylist can anticipate.
    """
    if not fname.endswith(".py"):
        return False            # FE/.html + the app spine are AGENT-built; they ghost
    stem = fname[:-3]
    if any(s in stem for s in _FLOOR_DEPRECATED_SUBSTR):
        return False            # deprecated: the student model owns scoring
    if stem.startswith(_FLOOR_TASK_PREFIXES):
        return True             # wire_/integrate_/verify_/build_ = real work
    if stem.endswith(_FLOOR_ROLE_SUFFIXES):
        return True             # <domain>_<role> = a module named for what it does
    return False                # everything else: prose, placeholders, nouns


def _checkout_drift_note() -> str:
    """One line distinguishing a genuinely spent anchor from a stale checkout.

    FU-032: on 2026-07-20 the floor printed "This needs a human: extend
    PRODUCT_SPEC" while the spec HAD been extended 17h earlier (#1639, PHASE
    8b) -- the runtime clone was simply 22 commits behind, so the anchor was
    not spent and the fix was a deploy, not spec authorship. The floor cannot
    tell those apart and asserted the wrong one, sending a human to write a
    spec that already existed.

    Uses `git ls-remote` rather than `origin/main`: the runtime clone has no
    refs/remotes/origin/main (only feature branches), so `git rev-parse
    origin/main` fails there (FU-028). Never raises, never blocks: this only
    decorates a log line, so every fault degrades to UNKNOWN.
    """
    def _git(*args: str, timeout: int = 10) -> str:
        return subprocess.run(
            ("git", *args), cwd=str(SENTINEL_DIR), capture_output=True,
            text=True, timeout=timeout,
        ).stdout.strip()

    try:
        head = _git("rev-parse", "HEAD")[:8]
    except Exception:
        return "CHECKOUT: UNKNOWN (git unavailable) -- verify the runtime clone is current before extending the spec."
    if not head:
        return "CHECKOUT: UNKNOWN (not a git checkout) -- verify the runtime clone is current before extending the spec."

    try:
        line = _git("ls-remote", "origin", "refs/heads/main", timeout=20)
        remote = line.split()[0][:8] if line else ""
    except Exception:
        remote = ""
    if not remote:
        return (f"CHECKOUT: HEAD={head}, origin/main=UNREACHABLE -- cannot tell a spent "
                f"anchor from a stale checkout; check the deploy before extending the spec.")
    if remote == head:
        return (f"CHECKOUT: HEAD={head} == origin/main -- the checkout IS current, so the "
                f"anchor is genuinely spent and this really does need a spec extension.")

    # The remote commit is usually NOT in the local object store (no fetch has
    # happened), in which case rev-list cannot count. Say so rather than
    # printing a "?" that reads like a real number.
    try:
        behind = _git("rev-list", "--count", f"{head}..{remote}", timeout=20)
    except Exception:
        behind = ""
    gap = f"behind by {behind} commits" if behind else "behind by an uncounted number of commits (no local fetch)"
    return (f"CHECKOUT: HEAD={head} != origin/main={remote} ({gap}) -- "
            f"the anchor may NOT be spent; this checkout may simply predate the refill. "
            f"DEPLOY FIRST (safe_ff.sh), then re-check before extending the spec.")


def _starvation_floor() -> int:
    """THE INVARIANT: the builder's queue is NEVER empty.

    WHY THIS EXISTS
    ---------------
    On 2026-07-14 the factory was found with proposed=0, pending=0 and the
    builder idle for 178 consecutive cycles -- no build PR in 13 hours. The
    architect had been returning +0 for a day: it burned its whole turn budget
    on read_* tools re-fetching context it had ALREADY been handed, and never
    reached propose_directive.

    The chairman's standing rule is "directives must NEVER be empty". Until now
    that rule lived in prose, and was enforced by a human noticing and
    hand-seeding directives. That IS the failure -- the same class as a rescore
    that only runs when someone remembers to fire it.

    So: the architect converging is NOT a precondition for the factory running.
    If the queue hits zero, we seed it DETERMINISTICALLY from the spec/KL gaps
    map (anchor_refill.mine_candidates -- the same miner the architect's own
    gaps map is built from), using the real spec paragraph as the build spec.

    This is a FLOOR, not a replacement: it only fires when the queue is empty,
    it seeds a handful, and a converging architect makes it dormant forever.
    An empty queue is now a bug the code fixes, not a bug a human discovers.
    """
    if not FLOOR_ON:
        return 0
    depth = _count_proposed() + _count_pending()
    if depth > 0:
        return 0                      # queue has work; the floor stays dormant

    log.error("STARVATION: proposed=0 pending=0 -- the builder has NOTHING to "
              "build. Seeding %d directive(s) deterministically from the gaps "
              "map. (The architect converging is not a precondition for the "
              "factory running.)", FLOOR_SEED_N)
    try:
        from zo_sentinel import anchor_refill as ar
    except Exception as e:                                  # pragma: no cover
        log.error("STARVATION FLOOR UNAVAILABLE: cannot import anchor_refill "
                  "(%s) -- the queue stays EMPTY. This is the worst state; fix "
                  "the import.", e)
        return 0

    try:
        sources = [SENTINEL_DIR / "PRODUCT_SPEC.md",
                   SENTINEL_DIR / ar.AUTO_ANCHOR_NAME]
        sources = [p for p in sources if p.exists()]
        exclude = ar._disk_names(SENTINEL_DIR) | _existing_anywhere()
        stems = _queued_stems()
        exclude |= stems | {s + ".py" for s in stems}
        terminal = ar._terminal_stems(SENTINEL_DIR / "directives", None)
        cands = ar.mine_candidates(sources, exclude, terminal)
    except Exception as e:
        log.error("STARVATION FLOOR: candidate mining failed (%s)", e)
        return 0

    cands = [c for c in cands if _is_seedworthy(c["file"])]
    if not cands:
        log.error("STARVATION FLOOR: gaps map is EXHAUSTED -- no unbuilt "
                  "spec-named .py targets remain. The queue stays empty and the "
                  "builder stays idle. This needs a human: extend PRODUCT_SPEC "
                  "or the roadmap anchor. %s", _checkout_drift_note())
        return 0

    PROPOSED_DIR.mkdir(parents=True, exist_ok=True)
    seeded = 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    for c in cands[:FLOOR_SEED_N]:
        name = c["file"]
        task = name[:-3]                                   # strip .py
        spec = (c.get("desc") or "").strip()
        # A thin description GHOSTS -- goose builds from `description` and
        # nothing else. Ground it in the real spec paragraph and pin the
        # house rules the builder lane requires.
        desc = (
            f"STARVATION-FLOOR SEED ({stamp}): the directive queue was EMPTY and "
            f"the builder was idle, so this target was mined deterministically "
            f"from {c.get('source','the spec')} line {c.get('line','?')}.\n\n"
            f"SPEC CONTEXT (verbatim from the roadmap):\n{spec}\n\n"
            f"BUILD {name} as a SELF-CONTAINED module mirroring a working "
            f"exemplar (read server_detail_api.py for a FastAPI read-only "
            f"router, or nvd_cve2_feed_loader.py for a daemon). REQUIREMENTS: "
            f"(1) state the exact public interface and return shapes; (2) app "
            f"code uses the app SQLAlchemy session (app/db.py, app/models.py) -- "
            f"daemons use write_service at http://127.0.0.1:8772/query and "
            f"/write with {{'table','rows','wait'}}, NEVER duckdb and NEVER a "
            f"direct DB connection; (3) the 7 axes in mcp_llm_axis_scores are "
            f"EXACTLY overall_risk, auth_strength, capability_breadth, "
            f"data_sensitivity, network_egress, maintainer_trust, "
            f"exploit_surface -- invent no columns or axes; (4) read-only unless "
            f"the spec says otherwise; (5) NO stubs, NO TODOs, NO placeholder "
            f"returns -- a hollow file is refused at the builder seam. "
            f"ACCEPTANCE: a __main__ block with asserts that proves it works "
            f"and prints PASS."
        )
        payload = {
            "task": task,
            "handler": "generate_file",
            "output_file": name,
            "complexity": "medium",
            "priority": 0.80,
            "description": desc,
            "reads": ["server_detail_api.py", "app/db.py", "app/models.py"],
            "rationale": "starvation floor: builder queue was empty",
            "next_directive": {},
        }
        out = PROPOSED_DIR / f"floor_{stamp}_{task}.json"
        try:
            out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
            seeded += 1
            log.warning("STARVATION FLOOR seeded: %s (from %s:%s)",
                        task, c.get("source"), c.get("line"))
        except Exception as e:
            log.error("STARVATION FLOOR: could not write %s (%s)", out.name, e)

    log.error("STARVATION FLOOR: seeded %d directive(s); the queue is no longer "
              "empty. The architect remains the primary source -- this is a "
              "floor, not a replacement.", seeded)
    return seeded


def _try_import_layer1():
    """Layer 1 knowledge sources, same module the legacy generator uses."""
    try:
        sys.path.insert(0, str(SENTINEL_DIR))
        import directive_knowledge_sources as dks  # type: ignore
        return dks.assemble_layer1_context()
    except Exception as e:
        log.warning("Layer 1 unavailable: %s", e)
        return {
            "product_spec":  "[Layer 1 unavailable]",
            "wiring_map":    "[Layer 1 unavailable]",
            "gaps_map":      "[Layer 1 unavailable]",
            "quality_map":   "[Layer 1 unavailable]",
        }


def _try_load_schema() -> dict:
    try:
        sys.path.insert(0, str(SENTINEL_DIR))
        import sentinel_directive_generator as sdg  # type: ignore
        return sdg.load_schema()
    except Exception as e:
        log.warning("schema load failed: %s", e)
        return {}


def _query_recent_failures() -> list:
    """Read-only fetch of last 50 escalation/build failure rows."""
    sql = (
        "SELECT content, created_at FROM mesh_memory "
        "WHERE memory_type IN ('escalation_call', 'build_failure') "
        "AND created_at > NOW() - INTERVAL 24 HOUR "
        "ORDER BY created_at DESC LIMIT 50"
    )
    try:
        r = requests.get(WS_QUERY_URL, params={"sql": sql}, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.warning("ws_query failures: %s", e)
    return []


def _existing_modules() -> list:
    """Live-graph dedup signal: distinct module files already in the code graph,
    so the architect proposes NOVEL capabilities instead of re-proposing modules
    that already exist (the static read_already_built() set goes stale, which is why
    recently-built files keep getting re-proposed). Bus-routed (/query on :8772, NOT
    direct duckdb), best-effort -> [] on any error. One query per cycle, and only on
    an idle cycle (see _builder_idle), so it adds no lock/herd pressure."""
    # Precise dedup universe: distinct .py MODULE basenames only. The graph also
    # indexes directives/*.done.json, directives_archive/* and breaker_actions/* --
    # those are artifacts, not modules an architect would propose, so we exclude
    # them (drops ~1600 noise names, leaving ~1000 real modules). Basename DISTINCT
    # is done in-SQL so the cap bounds modules, not alpha-early full paths.
    sql = ("SELECT DISTINCT regexp_replace(source_file, '^.*/', '') AS mod "
           "FROM code_nodes "
           "WHERE source_file LIKE '%.py' "
           "AND source_file NOT LIKE 'directives/%' "
           "AND source_file NOT LIKE 'directives_archive/%' "
           "AND source_file NOT LIKE 'breaker_actions/%' "
           "ORDER BY mod LIMIT 1200")
    try:
        # POST json body -- matches the builder's proven ws_query transport,
        # which returns {"rows": [...]} (the shape parsed below). One serialized
        # read on the same :8772 writer; best-effort.
        r = requests.post(WS_QUERY_URL, json={"sql": sql}, timeout=8)
        if r.status_code == 200:
            data = r.json()
            rows = data.get("rows", []) if isinstance(data, dict) else data
            mods = sorted({str(row.get("mod", "")).strip()
                           for row in rows
                           if isinstance(row, dict) and row.get("mod")})
            return [m for m in mods if m]
    except Exception as e:
        log.warning("ws_query existing_modules: %s", e)
    return []


def _builder_idle() -> bool:
    """True when the BUILD side is quiet -- no NEW build_artifact emitted in the
    last IDLE_MIN minutes.

    Keys on build_artifact EMISSION (mesh_memory) NOT raw DIRECTIVE_* events: the
    edit-class wire_*/integrate_* directives emit DIRECTIVE_COMPLETE but produce NO
    build_artifact by design (declared_output is None). Keying on DIRECTIVE_* (the
    prior behaviour) let that constant edit churn read as "builder active" every
    cycle and DEFER generation indefinitely -> novel CREATE proposals starved
    (verified live 2026-06-14). A build_artifact row means a real CREATE build just
    landed, so defer for IDLE_MIN to keep the graph read + goose subprocess from
    contending with the active build chain (the anti-herd intent is preserved).

    The generator is a BATCH process. Bus-routed, best-effort; returns True (idle)
    on any error so a check fault never STARVES generation -- strictly no regression
    vs the prior fail-open behaviour."""
    sql = ("SELECT MAX(created_at) AS last_at FROM mesh_memory "
           "WHERE memory_type = 'build_artifact'")
    try:
        r = requests.post(WS_QUERY_URL, json={"sql": sql}, timeout=8)
        if r.status_code == 200:
            data = r.json()
            rows = data.get("rows", []) if isinstance(data, dict) else data
            last = rows[0].get("last_at") if rows and isinstance(rows[0], dict) else None
            if not last:
                return True
            dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0 >= IDLE_MIN
    except Exception as e:
        log.warning("ws_query idle-check: %s", e)
    return True


def _cap_layer1(l1):
    """Cap each layer1 knowledge-map field to LAYER1_FIELD_CAP chars so the base
    ctx stays small enough for the slow ladder architect to finish within the goose
    timeout. Best-effort: non-dict / non-str values pass through unchanged."""
    if not isinstance(l1, dict):
        return l1
    return {k: (v[:LAYER1_FIELD_CAP] if isinstance(v, str) else v) for k, v in l1.items()}


_DONE_DIR = Path(os.environ.get(
    "DGG_DONE_DIR", "/home/workspace/zo_sentinel/directives/done"))


def _recent_built_modules(limit: int = 600) -> list:
    """Module basenames of the most-recently-built directives (directives/done/*.json
    by mtime, newest first). The architect keeps re-proposing RECENT builds because the
    graph dedup list is ordered alphabetically and the budget trim buries recents past
    the cutoff. Surfacing these FIRST in the budgeted avoid-list is the targeted fix for
    'recently-built files keep getting re-proposed'. Best-effort -> [] on any error."""
    try:
        files = sorted(_DONE_DIR.glob("*.json"),
                       key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
    except Exception:
        return []
    out = []
    for f in files:
        try:
            d = json.loads(f.read_text())
            of = (d.get("output_file") or "").strip()
            if of:
                out.append(of.rsplit("/", 1)[-1])   # basename, matches graph form
        except Exception:
            continue
    return out


def _recent_proposals(days: int = 3, cap: int = 25) -> list:
    """The architect's OWN recent proposals (mesh_memory directive_proposed), newest
    first, de-duplicated -- its cross-cycle COVERAGE memory, INCLUDING proposals since
    rejected/dropped that no longer sit in proposed/. Lets it stop re-circling the same
    subjects and range elsewhere. Bus-routed, best-effort -> []."""
    sql = ("SELECT content FROM mesh_memory WHERE memory_type='directive_proposed' "
           f"AND created_at > NOW() - INTERVAL {int(days)} DAY "
           "ORDER BY created_at DESC LIMIT 120")
    try:
        r = requests.post(WS_QUERY_URL, json={"sql": sql}, timeout=8)
        rows = r.json().get("rows", []) if r.ok else []
    except Exception:
        return []
    seen, out = set(), []
    for row in rows:
        try:
            task = json.loads(row.get("content") or "{}").get("task")
        except Exception:
            task = None
        if task and task not in seen:
            seen.add(task); out.append(task)
        if len(out) >= cap:
            break
    return out


def _trim_schema(schema):
    """Strip the stale static 'Already Built' + 'High-Value Targets'(snow/aidr)
    tail from the legacy schema doc before it enters the architect context.

    That tail (1) duplicates the live, fresher already_built_modules list and
    (2) re-advertises the PARKED snow/aidr Phase-9 branch as "high-value targets"
    -- together they fixated the architect on already-built / parked work (the
    +0 / ~5-subject loop). We keep the invariant HEAD (technology + wiring rules,
    DB-schema gotchas, directive JSON format, DO-NOT-GENERATE guardrails) and cut
    from the '## Already Built' marker onward. Best-effort: non-str or
    marker-absent -> returned unchanged.
    """
    if not isinstance(schema, str):
        return schema
    idx = schema.find("## Already Built")
    return schema[:idx].rstrip() + "\n" if idx != -1 else schema


def build_context() -> dict:
    ctx = {
        "schema":            _trim_schema(_try_load_schema()),
        "layer1":            _cap_layer1(_try_import_layer1()),
        "recent_failures":   _query_recent_failures(),
        "proposed_depth":    _count_proposed(),
        "recent_proposals":  _recent_proposals(),
        "generated_at":      _now_iso(),
    }
    # Fit the live-graph module list into whatever ctx budget remains so the
    # downstream json.dumps[:CTX_CAP] truncation never silently eats the list
    # tail (its at-risk end -- recently-built modules) OR a later field. We add
    # the modules LAST and trim them to the remaining bytes, not the recipe.
    # Recency-first: the architect re-proposes RECENTLY-built modules, so put the
    # newest builds at the head of the budgeted avoid-list -- the alphabetical graph
    # order buried recents past the budget cutoff, leaving the architect blind to them.
    graph_mods = _existing_modules()
    recent = _recent_built_modules()
    _seen, mods = set(), []
    for _m in recent + graph_mods:
        if _m and _m not in _seen:
            _seen.add(_m); mods.append(_m)
    if mods:
        used = len(json.dumps(ctx, default=str))
        budget = max(0, CTX_MODULE_BUDGET - used)
        kept, size = [], 0
        for m in mods:
            size += len(m) + 4            # quotes + comma overhead
            if size > budget:
                break
            kept.append(m)
        if len(kept) < len(mods):
            log.info("already_built_modules trimmed to %d/%d for ctx budget",
                     len(kept), len(mods))
        ctx["already_built_modules"] = kept
    else:
        ctx["already_built_modules"] = []
    return ctx


# ---------------------------------------------------------------------------
# Goose invocation
# ---------------------------------------------------------------------------

def _ensure_goose_env() -> None:
    """Select the ARCHITECT's goose provider (architect-scoped, so the builder
    goose_runner stays on the shim ladder regardless).

    Default ``openai`` = the API-key ladder shim on :8796 (today's behavior --
    builder + architect share it). Set ZO_ARCHITECT_PROVIDER to an OAuth-subscription
    provider (e.g. ``gemini_oauth``) to point ONLY the architect at a stronger,
    higher-context model via your own cached OAuth session -- goose handles that
    auth itself, so no base_url/key is needed (and the shim is bypassed for this
    role). Reversible: unset the var -> back to the shim. The OAuth session must be
    pre-authenticated once (``goose configure`` -> Google Gemini) and must refresh
    non-interactively for the headless daemon."""
    prov = os.environ.get("ZO_ARCHITECT_PROVIDER", "openai").strip() or "openai"
    os.environ["GOOSE_PROVIDER"] = prov
    if prov == "openai":
        # Architect-scoped MODEL on the ladder shim. Default MiniMax-Text-01 (rung 0, weak)
        # -- the +0/fixation bottleneck. Set ZO_ARCHITECT_MODEL to a ladder alias to give the
        # architect a STRONGER rung via the shim WITHOUT OAuth (Gemini OAuth is dead on this
        # headless host). Recommended: zo-ladder-cerebras = Cerebras gpt-oss-120b (free,
        # tool-calling, the bake-off winner) with free-rung failover. Reversible: unset -> MiniMax.
        # Default to a CAPABLE tool-calling rung. The MiniMax rung-0 aliases
        # (MiniMax-Text-01 / zo-ladder-v1) are the documented +0/tool-loop bottleneck;
        # treat them (and unset) as "weak" and upgrade to Cerebras gpt-oss-120b (free,
        # tool-calling, free-rung failover). Override with any OTHER ZO_ARCHITECT_MODEL.
        _am = (os.environ.get("ZO_ARCHITECT_MODEL", "") or "").strip()
        if _am.lower() in ("", "minimax-text-01", "zo-ladder-v1"):
            _am = "zo-ladder-cerebras"
        # Capable-rung ROTATION on consecutive non-convergence (mirrors
        # build_routing._CAPABLE for the builder). Env-tunable, reversible:
        # DGG_ROTATE_AFTER=0 disables; rotation list override via
        # ZO_ARCHITECT_MODEL_ROTATION (comma-separated ladder aliases).
        _rot_after = int(os.environ.get("DGG_ROTATE_AFTER", 2))
        _rotation = [m.strip() for m in os.environ.get(
            "ZO_ARCHITECT_MODEL_ROTATION",
            "zo-ladder-cerebras,zo-ladder-nvidia,zo-ladder-mistral,zo-ladder-groq",
        ).split(",") if m.strip()]
        _picked = _rotated_model(_am, _consec_nonconverge, _rot_after, _rotation)
        if _picked != _am:
            log.warning("architect rung ROTATION: %s -> %s after %d consecutive "
                        "non-converged cycles", _am, _picked, _consec_nonconverge)
            _am = _picked
        os.environ["GOOSE_MODEL"] = _am
        os.environ.setdefault("OPENAI_BASE_URL", "http://127.0.0.1:8796/v1")
        os.environ.setdefault("OPENAI_API_KEY",  "dummy_key_for_shim")  # set => goose skips keyring

    # When the architect runs a NON-default (versioned) goose binary, give it an
    # ISOLATED config/data home. A different goose version PANICS at startup
    # (`thread 'sq...'`) reading the ~/.config/goose + session store the builder's
    # goose wrote -- version skew on the on-disk store. The canary proved 1.38 runs
    # clean against a FRESH store, so we replicate that: point this goose at its own
    # XDG dirs (env-driven provider config still applies; no config file needed).
    if ARCHITECT_GOOSE_BIN != "goose":
        iso = os.environ.get("ZO_ARCHITECT_GOOSE_HOME", "/home/workspace/.goose_architect")
        try:
            os.makedirs(iso, exist_ok=True)
        except Exception:
            pass
        os.environ["HOME"]            = iso
        os.environ["XDG_CONFIG_HOME"] = f"{iso}/.config"
        os.environ["XDG_DATA_HOME"]   = f"{iso}/.local/share"
        os.environ["XDG_STATE_HOME"]  = f"{iso}/.local/state"
        # RETIRED (#447 hook): PreToolUse hooks do NOT fire for stdio-MCP extension tools
        # (the zo_directive_bridge reads the architect loops on) -- only for builtins -- so the
        # tool-budget deny never bound and the live cycle still ran to the 240s timeout (+0).
        # Convergence is now enforced by the CLI --max-turns cap on the goose argv (see
        # ARCHITECT_MAX_TURNS / run_goose_cycle), which IS enforced in goose's core loop for
        # extension tools. The architect_budget plugin is left in-tree but no longer deployed.


# Consecutive architect cycles that did NOT converge to a propose_directive
# (+0 or timeout). Reset on any +N cycle. Drives capable-rung rotation so a
# single weak/flaky rung can never silently starve the queue (2026-07-12:
# cerebras +0 for ~13h, proposed/ empty, builder idle).
_consec_nonconverge = 0


def _rotated_model(am: str, consec: int, rot_after: int, rotation: list) -> str:
    """Pure rung-rotation policy (unit-testable, no env access).

    After ``rot_after`` consecutive non-converged cycles, step through
    ``rotation`` (one step per further ``rot_after`` cycles), starting from
    the home rung ``am``. rot_after<=0 disables rotation. If ``am`` is not in
    the rotation list it is prepended as the home rung, so an explicit
    chairman override still participates (and recovers) instead of pinning."""
    if rot_after <= 0 or not rotation or consec < rot_after:
        return am
    rot = list(rotation)
    if am not in rot:
        rot = [am] + rot
    home = rot.index(am)
    step = (consec - rot_after) // rot_after + 1
    return rot[(home + step) % len(rot)]


def _emit_nonconvergence(secs, delta, rc, kind: str) -> None:
    """LOUD failure when an architect cycle does NOT converge to a directive (timeout, or
    rc=0 but +0). The model burned ~secs of tool calls without landing a propose_directive
    -- a goose tool-call LOOP / over-exploration, NOT a silent timeout. Emits a
    failure_classifier-catchable log line + a best-effort mesh_memory row so loop_watch /
    pipeline-watch surface it. (Stop + emit-a-failure-log, per the convergence-guard design.)"""
    global _consec_nonconverge
    _consec_nonconverge += 1
    model = os.environ.get("GOOSE_MODEL", "?")
    log.error("ARCHITECT NON-CONVERGENCE (%s): goose [%s] model=%s ran ~%ss, proposed +%d "
              "-- did NOT reach propose_directive (tool-call loop / over-exploration); rc=%s",
              kind, ARCHITECT_GOOSE_BIN, model, secs, max(delta or 0, 0), rc)
    try:
        import requests
        requests.post("http://127.0.0.1:8772/write",
                      json={"table": "mesh_memory", "rows": [{
                          "agent_id": "directive_architect",
                          "memory_type": "directive_gen_failure",
                          "content": json.dumps({"kind": kind, "secs": secs,
                                                 "delta": delta, "rc": rc, "model": model,
                                                 "bin": ARCHITECT_GOOSE_BIN}),
                          "created_at": _now_iso()}], "wait": False},
                      timeout=5)
    except Exception:
        pass


def _salvage_transcript(stdout_text) -> int:
    """Recover directives the harness discarded, on BOTH total-loss paths.

    Called from the delta<=0 branch (fenced blocks the model emitted without
    reaching the tool call) AND from the TimeoutExpired branch (goose's own
    rendered propose_directive calls, killed by the wall clock mid-flight).
    Both are transcripts that were already going to be thrown away, so salvage
    can never displace or race a converged tool call.

    Returns the number of directives written into PROPOSED_DIR. Never raises:
    a salvage failure must not take down the generator loop.
    """
    if not stdout_text:
        return 0
    try:
        from zo_sentinel.arch_salvage import salvage
    except Exception:
        try:
            from arch_salvage import salvage
        except Exception as e:
            log.error("SALVAGE UNAVAILABLE: cannot import arch_salvage (%s)", e)
            return 0
    try:
        PROPOSED_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

        def _writer(fname, payload):
            (PROPOSED_DIR / fname).write_text(
                json.dumps(payload, indent=1), encoding="utf-8")

        return salvage(
            stdout_text,
            queued_stems=_queued_stems(),
            existing_files=_existing_anywhere(),
            stamp=stamp,
            writer=_writer,
            log=lambda m: log.warning("%s", m),
        )
    except Exception as e:
        log.error("SALVAGE FAILED: %s", e)
        return 0


def run_goose_cycle() -> dict:
    """Invoke goose with the directive_architect recipe. Return summary dict."""
    if not RECIPE_PATH.exists():
        log.error("recipe missing: %s", RECIPE_PATH)
        return {"status": "skipped", "reason": "recipe_missing"}

    depth = _count_proposed()
    # Cap resolved through the declarative policy layer per cycle (live-
    # tunable, no restart): env DGG_MAX_PROPOSED_DEPTH > durable override >
    # policy_defaults.toml. Fail-open to the import-time constant.
    try:
        from zo_sentinel import policy as _policy
        _cap = int(_policy.value("architect.max_proposed_depth"))
    except Exception:
        _cap = MAX_PROPOSED
    if depth >= _cap:
        log.info("proposed/ depth %d >= cap %d; skipping cycle", depth, _cap)
        return {"status": "skipped", "reason": "depth_cap", "depth": depth}

    # Batch-when-idle: only generate when the build side is quiet, so the graph
    # read + goose subprocess never contend with an active build. Herd-safe.
    if IDLE_GATE and not _builder_idle():
        log.info("builder active (build_artifact within %dm); deferring generation", IDLE_MIN)
        return {"status": "skipped", "reason": "builder_busy"}

    # Self-refilling anchor (gated, fail-open, lazy): when the live gaps map is
    # nearly exhausted, deterministically mine the Graphify KL docs
    # (docs/DESIGN_*.md, roadmap) for new candidates BEFORE building this
    # cycle's context, so the architect never faces an empty anchor -- the
    # anchor-exhaustion class where it burns its turn budget inventing
    # near-duplicate proposals (observed 2026-07-02 14:05 UTC). No-op unless
    # directives/.anchor_refill_on is "1" (or ZO_ANCHOR_REFILL=1); a full
    # anchor is never touched; faults never block generation.
    try:
        from zo_sentinel import anchor_refill as _refill
        if _refill.enabled(SENTINEL_DIR / "directives"):
            _rstats = _refill.run_refill(SENTINEL_DIR)
            if _rstats.get("appended"):
                log.info("anchor refill: %s", _rstats)
    except Exception as _re:
        log.warning("anchor refill unavailable/failed (fail-open): %s", _re)

    ctx = build_context()
    ctx_json = json.dumps(ctx, default=str)[:60000]   # MiniMax prompt cap headroom

    log.info("invoking goose [%s] (ctx %d bytes, proposed_depth=%d)", ARCHITECT_GOOSE_BIN, len(ctx_json), depth)
    _ensure_goose_env()
    _t0 = time.time()
    try:
        argv = [ARCHITECT_GOOSE_BIN, "run", "--recipe", str(RECIPE_PATH),
                "--params", f"context_json={ctx_json}"]
        if ARCHITECT_MAX_TURNS > 0:
            # Hard cap enforced inside goose's core agent loop -- canary-proven (1.38) to
            # bound the stdio-MCP bridge-tool loop the architect over-explores on, where the
            # recipe settings.max_turns field AND the PreToolUse hook both failed to. Turns a
            # 240s over-exploration burn into a bounded attempt that still reaches propose.
            argv += ["--max-turns", str(ARCHITECT_MAX_TURNS)]
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=GOOSE_TIMEOUT,
            cwd=str(SENTINEL_DIR),
        )
        rc = proc.returncode
        # Goose writes its proposed/ JSONs via the directive_mcp tool calls;
        # we don't need to parse stdout. Just count what landed.
        new_depth = _count_proposed()
        delta = new_depth - depth
        # Full transcript dump (debug): the truncated log tail hides the tool RESULT between
        # a propose_directive render and the model's reply. Persist the whole exchange so a +0
        # is fully diagnosable (did the bridge tool execute? what did it return?).
        try:
            with open("/home/workspace/logs/architect_last_goose.txt", "w") as _df:
                _df.write("rc=%s delta=%s\n=== STDOUT ===\n%s\n=== STDERR ===\n%s\n"
                          % (rc, delta, proc.stdout or "", proc.stderr or ""))
        except Exception:
            pass
        if proc.stderr:
            log.warning("goose stderr[:1500]: %s", proc.stderr[:1500].replace("\n", " | "))
        _elapsed = int(time.time() - _t0)
        log.info("goose returned rc=%d in %ds; proposed_depth %d -> %d (+%d)",
                 rc, _elapsed, depth, new_depth, delta)
        if delta > 0:
            global _consec_nonconverge
            _consec_nonconverge = 0
        if delta <= 0:
            # Log the model's transcript tail so a +0 is diagnosable (proposed & rejected?
            # hit the --max-turns cap mid-explore? emitted inline text instead of a tool call?).
            if proc.stdout:
                log.warning("goose stdout[-2200:] (non-converge transcript tail): %s",
                            proc.stdout[-2200:].replace("\n", " | "))
            # The transcript is not noise: the architect routinely emits
            # well-formed directive objects in fenced blocks and simply never
            # reaches propose_directive. Discarding it starves the builder and
            # makes the starvation floor report an EXHAUSTED gaps map that is
            # not actually exhausted. Recover before declaring non-convergence.
            _recovered = _salvage_transcript(proc.stdout)
            if _recovered:
                new_depth = _count_proposed()
                delta = new_depth - depth
                log.warning(
                    "ARCHITECT SALVAGE: recovered %d directive(s) the harness "
                    "would have discarded; proposed_depth now %d. The model "
                    "CONVERGED on content and failed only the tool call.",
                    _recovered, new_depth)
            _emit_nonconvergence(_elapsed, delta, rc, "zero_proposed"
                                 if not _recovered else "zero_proposed_salvaged")
        return {
            "status": "ok" if rc == 0 else "goose_nonzero",
            "rc": rc,
            "proposed_delta": delta,
            "proposed_depth_after": new_depth,
        }
    except subprocess.TimeoutExpired as _te:
        log.error("goose TIMEOUT after %ds", GOOSE_TIMEOUT)
        if getattr(_te, "stdout", None):
            _so = _te.stdout if isinstance(_te.stdout, str) else _te.stdout.decode("utf-8","replace")
            log.warning("goose stdout[-2200:] (timeout transcript tail): %s", _so[-2200:].replace("\n"," | "))
        # A timeout transcript is STRONGER evidence than a fenced one: goose
        # had REACHED propose_directive and the wall clock beat it. Discarding
        # it wholesale threw away completed tool calls (observed 12:20:53Z
        # 2026-07-29, two of them). Salvage before declaring non-convergence.
        _to_out = getattr(_te, "stdout", None)
        if _to_out is not None and not isinstance(_to_out, str):
            _to_out = _to_out.decode("utf-8", "replace")
        _recovered = _salvage_transcript(_to_out)
        if _recovered:
            log.warning(
                "ARCHITECT SALVAGE (timeout): recovered %d directive(s) from a "
                "transcript that reached propose_directive before the %ds wall "
                "clock. The builder is not starved by an exhausted anchor.",
                _recovered, GOOSE_TIMEOUT)
        _emit_nonconvergence(GOOSE_TIMEOUT, 0, None,
                             "timeout_salvaged" if _recovered else "timeout")
        return {"status": "timeout", "salvaged_timeout": _recovered}
    except FileNotFoundError:
        log.error("goose CLI not on PATH; daemon cannot continue")
        return {"status": "no_goose"}
    except Exception as e:
        log.error("goose invocation error: %s", e)
        return {"status": "error", "error": str(e)}


def _record_cycle(summary: dict) -> None:
    """Best-effort write_service log row for forensics."""
    try:
        requests.post(HEARTBEAT_URL, json={
            "table": "mesh_memory",
            "rows": [{
                "agent_id": SERVICE_NAME,
                "memory_type": "directive_generation_goose",
                "content": json.dumps(summary),
                "importance": 0.6,
                "created_at": _now_iso(),
            }],
            "wait": False,
        }, timeout=5)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _handle_signal(signum, _frame):
    log.info("received signal %d; requesting stop", signum)
    _stop_requested.set()


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    log.info("=" * 60)
    log.info("ZO-SENTINEL Directive Generator (Goose-driven) v0.1")
    log.info("  Recipe:        %s", RECIPE_PATH)
    log.info("  Writes to:     %s", PROPOSED_DIR)
    log.info("  Poll every:    %ds", POLL_SECS)
    log.info("  Heartbeat:     %ds", HEARTBEAT_SECS)
    log.info("  Goose timeout: %ds", GOOSE_TIMEOUT)
    log.info("  Proposed cap:  %d", MAX_PROPOSED)
    log.info("  Starvation floor: %s (seed %d when queue hits 0)",
             "ON" if FLOOR_ON else "OFF", FLOOR_SEED_N)
    log.info("=" * 60)

    hb = threading.Thread(target=_heartbeat_loop, daemon=True, name="hb")
    hb.start()

    # Immediate cycle on startup
    summary = run_goose_cycle()
    _record_cycle(summary)
    _starvation_floor()

    while not _stop_requested.is_set():
        _stop_requested.wait(POLL_SECS)
        if _stop_requested.is_set():
            break
        try:
            summary = run_goose_cycle()
            _record_cycle(summary)
            # The queue must never be empty -- enforced in code, every cycle,
            # whether or not the architect converged. See _starvation_floor().
            _starvation_floor()
        except Exception as e:
            log.error("cycle error: %s", e)

    log.info("clean shutdown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
