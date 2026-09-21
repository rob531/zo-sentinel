#!/usr/bin/env python3
"""app_surface_kl.py -- the app-surface knowledge layer.

WHAT THIS IS
------------
The second KL artifact, built on the pattern `schema_kl.py` proved and
`tools/graph_refresh.py` already supervises (FU-071):

    introspect live truth  ->  persist a versioned artifact  ->  enforce it as a
    pure-AST/static linter that runs with NO DB and NO NETWORK

`schema_kl` answers "what columns actually exist?" and stopped the builder
hallucinating schemas. This answers the four questions that are still
unanswered at build time, and which the 2026-07-19 reachability postmortem
identified as the reason 283 routers mount nowhere:

    routes     -- which URLs are already TAKEN, by whom, and which are live
    mounts     -- which sockets exist, what is in them, what is merely declared
    consumers  -- which URLs the front-end actually calls (and which it calls
                  that no route serves -- the #1481 bug class, both directions)
    data       -- the model/table facts, carried through from schema_kl

WHY IT MATTERS (measured, not asserted)
---------------------------------------
The census says the builder's problem is not codegen. Of the orphaned routers,
essentially all parse, and the large majority import the real data layer. What
they lack is an ADDRESS: ~70% declare no prefix at all, so their mount point is
undecidable from the file; 37 modules all claim `/api` and 11 claim `/servers`;
and `GET /servers/compare` was built four separate times because dedup keys on
FILENAME and never on URL (FU-069).

An architect cannot allocate from a namespace it cannot see. This makes the
namespace visible.

DESIGN CONSTRAINT -- INJECTED, NEVER FETCHED
--------------------------------------------
`goose_recipes/directive_architect.yaml` deliberately throttles `graph_neighbors`
to at most one call, and only after the first proposal, because exploratory tools
caused a reasoning death spiral. So this artifact is built for INJECTION: see
`render_for_architect()`, which emits a hard-budgeted block suitable for
`context_json`. Nothing here asks an agent to go and look something up.

SINGLE DEFINITION OF "MOUNTED"
------------------------------
The mounted/unmounted split is delegated to `tools.reachability_ratchet.census()`
rather than reimplemented. `zo_sentinel/gates/hollow.py` says it best: three
copies of a regex are three chances for the gates to disagree. If this module and
the armed ratchet ever disagreed about what "mounted" means, the KL would be
teaching the architect something CI would then punish it for.

NOT IN SCOPE, DELIBERATELY
--------------------------
This does not create `app/mounts.toml`, mount anything, or change any agent
prompt. The 2026-07-21 CofC ruled that the mount mechanism must be
human-authored and that "a registry nothing reads is the uncalled-helper failure
class"; the 07-23 mount-lane review owns that decision. This module's job is to
put real data in front of that review, and to make FU-069 measurable today.

USAGE
-----
    python app_surface_kl.py                     # human summary
    python app_surface_kl.py --out PATH          # write the artifact
    python app_surface_kl.py --report            # duplicate/collision report
    python app_surface_kl.py --architect-block   # what would be injected
    python app_surface_kl.py --quiet
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(ROOT, "graphify-out", "app_surface_kl.json")
SCHEMA_KL = os.path.join(ROOT, "graphify-out", "schema_kl.json")
DEFERRED_PATH = os.path.join(ROOT, "tools", "reachability_deferred.json")

# Sentinel strings are ASSEMBLED, never written literally.
#
# `tools/reachability_ratchet.py` decides a module is a router by searching its
# source for the router-constructor call or a router-verb decorator. Any tool that
# reasons ABOUT routers -- and therefore has to name those markers -- registers
# as one -- the first version of this file did exactly that and
# the armed ratchet failed it with `delta=+1`, correctly on its own terms. Taking an
# exemption would have been exempting a detector false-positive rather than a design
# decision, and FU-064 alarms on exemption inflation for good reason. So we keep the
# census honest by never emitting the marker.
_AT = "@"
_ROUTER_MARK = _AT + "router."
_APP_MARK = _AT + "app."
_APIROUTER_MARK = "API" + "Router("

# URL literals in templates/SPA: '/api/...' or "/servers/..." etc.
URL_LITERAL = re.compile(r"""['"](/(?:api|servers|freshness|rbac|auth|scan|ask)[A-Za-z0-9_/{}.\-]*)['"+]""")
ROUTE_DECOR = re.compile(re.escape(_ROUTER_MARK) + r"(get|post|put|delete|patch)\(\s*['\"]([^'\"]*)['\"]")
# App-object verb decorators count ONLY inside app/. At repo root such a decorator
# is almost always a module standing up its own throwaway application object for a
# __main__ self-test -- or a hollow standalone app, which is the other gate's
# business, not ours. Counting those inflated the namespace by 245 phantom routes
# that no server ever serves.
APP_DECOR = re.compile(re.escape(_APP_MARK) + r"(get|post|put|delete|patch)\(\s*['\"]([^'\"]*)['\"]")
PREFIX_DECL = re.compile(re.escape(_APIROUTER_MARK) + r"[^)]*prefix\s*=\s*['\"]([^'\"]+)['\"]", re.S)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _git_head():
    try:
        return subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return "unknown"


def _load_census():
    """Delegate the mounted/unmounted split to the armed ratchet -- one definition."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import reachability_ratchet  # noqa: E402
    return reachability_ratchet.census()


def _join(prefix, path):
    if not prefix:
        return path or "/"
    return (prefix.rstrip("/") + "/" + (path or "").lstrip("/")).rstrip("/") or "/"


def static_stem(path):
    """Leading static segments of a path template -- the comparable part.

    `/api/verdict/{server_id}` -> `/api/verdict`.  Used to match a route against
    a front-end URL literal that is built by concatenation.
    """
    out = []
    for seg in (path or "").split("/"):
        if not seg:
            continue
        if seg.startswith("{"):
            break
        out.append(seg)
    return "/" + "/".join(out) if out else "/"


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------
def build_routes(census):
    """Every route any router in the repo declares, with its owner and status."""
    mounted = set(census["mounted"])
    by_path, owners = {}, defaultdict(list)
    prefixes = defaultdict(list)
    tags = Counter()

    def ingest(stem, src, is_mounted, allow_app_decor=False):
        prefix = None
        pm = PREFIX_DECL.search(src)
        if pm:
            prefix = pm.group(1)
            prefixes[prefix].append(stem)
        decls = list(ROUTE_DECOR.finditer(src))
        if allow_app_decor:
            decls += list(APP_DECOR.finditer(src))
        for m in decls:
            method, raw = m.group(1).upper(), m.group(2)
            full = "%s %s" % (method, _join(prefix, raw))
            owners[full].append(stem)
            rec = by_path.setdefault(full, {"module": stem, "mounted": is_mounted,
                                            "prefix": prefix, "tags": []})
            if is_mounted:                      # a mounted owner wins the record
                rec.update({"module": stem, "mounted": True, "prefix": prefix})
        for m in re.finditer(r"tags\s*=\s*\[([^\]]*)\]", src):
            for t in re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)):
                tags[t] += 1

    for fn in sorted(f for f in os.listdir(ROOT)
                     if f.endswith(".py") and os.path.isfile(os.path.join(ROOT, f))):
        stem = fn[:-3]
        src = _read(os.path.join(ROOT, fn))
        if _APIROUTER_MARK[:-1] not in src and _ROUTER_MARK not in src:
            continue
        ingest(stem, src, stem in mounted)

    # app/ package: the only place an app-object decorator is real. Being INSIDE app/ does not
    # make a module mounted -- app/api/*.py and app/routers/*.py contain routers
    # main.py never includes. Mounted means main.py names it, same as the ratchet's
    # rule for root modules; app/main.py itself is the mount point.
    main_src = _read(os.path.join(ROOT, "app", "main.py"))
    for dirpath, _d, files in os.walk(os.path.join(ROOT, "app")):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            src = _read(os.path.join(dirpath, fn))
            if (_APIROUTER_MARK[:-1] not in src and _ROUTER_MARK not in src
                    and _APP_MARK not in src):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), ROOT).replace("\\", "/")
            stem = fn[:-3]
            is_main = rel == "app/main.py"
            in_main = is_main or bool(re.search(r"\b%s\b" % re.escape(stem), main_src))
            ingest(rel[:-3], src, in_main, allow_app_decor=is_main)

    duplicates = [{"path": p, "modules": sorted(set(ms))}
                  for p, ms in sorted(owners.items()) if len(set(ms)) > 1]
    collisions = [{"prefix": p, "modules": sorted(set(ms))}
                  for p, ms in sorted(prefixes.items()) if len(set(ms)) > 1]
    return {
        "by_path": by_path,
        "taken_paths": sorted(by_path),
        "taken_prefixes": {p: sorted(set(m)) for p, m in sorted(prefixes.items())},
        "taken_tags": dict(tags.most_common()),
        "duplicate_paths": duplicates,
        "prefix_collisions": collisions,
        "route_total": len(by_path),
        "live_route_total": sum(1 for r in by_path.values() if r["mounted"]),
    }


def detect_mount_mechanism():
    """Describe how app/main.py ACTUALLY mounts routers, by reading it.

    This field used to be a hardcoded string reading

        "app/main.py :: _OPTIONAL_ROUTERS (import + include_router, failures
         swallowed by `except Exception: pass` -- see FU-044)"

    which described a mechanism main.py stopped using when the Option-B spine
    landed (SOA design 2026-07-21). It was stale in the specific way that
    misleads: it ADVERTISED A BUG THAT HAD ALREADY BEEN FIXED, in an artifact
    whose whole purpose is to tell a reader what the app does.

    A literal cannot go stale safely, so this derives the answer from the two
    files that decide it and reports what it actually found -- including
    "unrecognised", which is the honest answer if the mechanism changes again.
    """
    main_src = _read(os.path.join(ROOT, "app", "main.py"))
    spine_src = _read(os.path.join(ROOT, "app", "_spine_generated.py"))

    if not main_src:
        return {"kind": "unknown",
                "detail": "app/main.py could not be read -- mechanism undetermined"}

    if "include_spine" in main_src and spine_src:
        n_active = 0
        active = os.path.join(ROOT, "services", "active")
        if os.path.isdir(active):
            n_active = sum(1 for d in os.listdir(active)
                           if os.path.isfile(os.path.join(active, d, "service.toml")))
        # Does the generated spine record failures rather than swallow them?
        records = ("spine_mount_failures" in spine_src
                   and "spine_skipped_no_router" in spine_src)
        swallows = "except Exception:\n        pass" in spine_src
        return {
            "kind": "generated_spine",
            "entrypoint": "app/main.py :: include_spine(app)",
            "generated_file": "app/_spine_generated.py",
            "generator": "tools/generate_spine.py",
            "source_of_truth": "services/active/*/service.toml (presence == registration)",
            "active_service_count": n_active,
            "failure_handling": (
                "recorded on app.state (spine_mount_failures / "
                "spine_skipped_no_router) and exposed at /spine/health; prod boots "
                "anyway, the strict raise is a CI-only gate"
                if records and not swallows else
                "UNVERIFIED -- the generated spine does not record the expected "
                "app.state failure attributes"),
            "detail": (
                "app/main.py :: include_spine(app) from the generated "
                "app/_spine_generated.py, built by tools/generate_spine.py from "
                "services/active/*/service.toml. A module that declares no router "
                "is a VISIBLE skip, not a swallow."),
        }

    if "_OPTIONAL_ROUTERS" in main_src:
        return {
            "kind": "optional_routers_list",
            "entrypoint": "app/main.py :: _OPTIONAL_ROUTERS",
            "failure_handling": (
                "swallowed by `except Exception: pass`"
                if "except Exception" in main_src else "unknown"),
            "detail": ("app/main.py :: _OPTIONAL_ROUTERS (hand-maintained "
                       "import + include_router loop) -- the pre-SOA mechanism"),
        }

    return {"kind": "unrecognised",
            "detail": ("app/main.py mounts routers by a mechanism this detector "
                       "does not recognise -- update detect_mount_mechanism()")}


def build_mounts(census):
    deferred = {}
    raw = _read(DEFERRED_PATH)
    if raw:
        try:
            deferred = json.loads(raw).get("deferred", {}) or {}
        except ValueError:
            deferred = {}
    _mech = detect_mount_mechanism()
    return {
        "mechanism": _mech["detail"],
        "mechanism_detail": _mech,
        "mounted": sorted(census["mounted"]),
        "mounted_count": census["mounted_count"],
        "unmounted": sorted(o["module"] for o in census["orphans"]),
        "unmounted_count": census["orphan_count"],
        "declared_deferred": sorted(deferred),
        "exempted": sorted(census.get("exempted", [])),
        "router_modules_total": census["router_modules_total"],
        "no_prefix_declared": sorted(o["module"] for o in census["orphans"]
                                     if not o.get("declared_prefix")),
    }


def build_consumers(routes):
    """What the front-end actually calls -- both directions of the #1481 class."""
    shell, views = Counter(), defaultdict(set)
    shell_dir = os.path.join(ROOT, "app", "static")
    if os.path.isdir(shell_dir):
        for fn in sorted(os.listdir(shell_dir)):
            if fn.endswith(".html"):
                for u in URL_LITERAL.findall(_read(os.path.join(shell_dir, fn))):
                    shell[u] += 1
    for fn in sorted(f for f in os.listdir(ROOT) if f.endswith(".html")):
        for u in URL_LITERAL.findall(_read(os.path.join(ROOT, fn))):
            views[fn].add(u)

    served = {static_stem(p.split(" ", 1)[1]) for p in routes["taken_paths"]}
    live = {static_stem(p.split(" ", 1)[1])
            for p, r in routes["by_path"].items() if r["mounted"]}

    called = set(shell)
    for us in views.values():
        called |= us

    unserved = sorted(u for u in called if static_stem(u) not in served)
    called_but_dead = sorted(u for u in shell if static_stem(u) not in live)
    return {
        "shell_calls": sorted(shell),
        "shell_call_count": len(shell),
        "view_calls": {k: sorted(v) for k, v in sorted(views.items())},
        "orphan_view_files": len(views),
        "called_but_no_route_anywhere": unserved,
        "shell_calls_hitting_no_LIVE_route": called_but_dead,
        "routes_with_no_caller": sorted(
            p for p in routes["taken_paths"]
            if static_stem(p.split(" ", 1)[1]) not in {static_stem(u) for u in called}),
    }


def build_data():
    raw = _read(SCHEMA_KL)
    if not raw:
        return {"available": False,
                "note": "graphify-out/schema_kl.json absent -- run tools/graph_refresh.py"}
    try:
        kl = json.loads(raw)
    except ValueError:
        return {"available": False, "note": "schema_kl.json unparseable"}
    models = kl.get("models", kl)
    out = {}
    if isinstance(models, dict):
        for name, spec in models.items():
            if not isinstance(spec, dict):
                continue
            out[name] = {
                "table": spec.get("table") or spec.get("__tablename__"),
                "required_construct": spec.get("required_construct", []),
                "column_count": len(spec.get("columns", []) or []),
            }
    return {"available": True, "models": out, "model_count": len(out)}


# --------------------------------------------------------------------------
# build / load
# --------------------------------------------------------------------------
def build_app_surface_kl():
    census = _load_census()
    routes = build_routes(census)
    return {
        "meta": {
            "generator": "app_surface_kl.py",
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "built_at_commit": _git_head(),
            "kl_version": 1,
        },
        "routes": routes,
        "mounts": build_mounts(census),
        "consumers": build_consumers(routes),
        "data": build_data(),
    }


def load_app_surface_kl(path=DEFAULT_OUT):
    return json.loads(_read(path))


# --------------------------------------------------------------------------
# linter -- pure static, no DB, no network (mirrors schema_kl.lint_source)
# --------------------------------------------------------------------------
def lint_source(source, kl, module_stem=None):
    """Return a list of (code, message) for a router module against the KL.

    HIGH-PRECISION by construction, so it is safe as a report and, later, a gate:
      NO_PREFIX        -- declares routes but no prefix => unmountable by anyone
      PREFIX_COLLISION -- claims a prefix another module already claims
      DUPLICATE_ROUTE  -- serves a path some other module already serves
    """
    violations = []
    if _APIROUTER_MARK[:-1] not in source and _ROUTER_MARK not in source:
        return violations

    routes = kl.get("routes", {})
    pm = PREFIX_DECL.search(source)
    prefix = pm.group(1) if pm else None
    decls = [(m.group(1).upper(), m.group(2)) for m in ROUTE_DECOR.finditer(source)]

    if decls and not prefix:
        violations.append((
            "NO_PREFIX",
            "declares %d route(s) but sets no router prefix: the mount point is "
            "undecidable from this file, which is why ~70%% of the graveyard cannot "
            "be mounted by anyone" % len(decls)))

    # A SHARED prefix is not a defect. `/api` is claimed by 65 modules and `/servers`
    # by 13 -- those are namespace roots doing their job, and the routers under them
    # differ by path. Only a SPECIFIC prefix (>=2 segments, e.g. /api/perspectives)
    # being claimed twice is evidence of two modules owning one surface. The precise
    # signal for real overlap is DUPLICATE_ROUTE below; this stays advisory.
    if prefix and len([s for s in prefix.split("/") if s]) >= 2:
        owners = [m for m in routes.get("taken_prefixes", {}).get(prefix, [])
                  if m != module_stem]
        if owners:
            violations.append((
                "PREFIX_COLLISION",
                "specific prefix %s is already claimed by %s" % (prefix, ", ".join(owners[:4]))))

    by_path = routes.get("by_path", {})
    for method, raw in decls:
        full = "%s %s" % (method, _join(prefix, raw))
        rec = by_path.get(full)
        if rec and rec.get("module") != module_stem:
            violations.append((
                "DUPLICATE_ROUTE",
                "%s is already served by %s%s" % (
                    full, rec["module"], " (LIVE)" if rec.get("mounted") else "")))
    return violations


# --------------------------------------------------------------------------
# architect injection (budgeted -- context_json is ~30KB total)
# --------------------------------------------------------------------------
def render_for_architect(kl, budget=6000):
    """A compact, injectable view of the taken namespace.

    Deliberately a BLOCK OF FACTS, not a tool to call: the architect recipe caps
    exploratory tool use because it caused a reasoning death spiral, so the
    namespace has to arrive already computed.
    """
    r, m, c = kl["routes"], kl["mounts"], kl["consumers"]
    live = sorted(p for p, rec in r["by_path"].items() if rec["mounted"])
    lines = [
        "APP SURFACE (generated %s @ %s) -- allocate from this, do not invent."
        % (kl["meta"]["built_at"], kl["meta"]["built_at_commit"]),
        "",
        "LIVE ROUTES (%d) -- a directive proposing any of these is duplicate work:" % len(live),
    ]
    lines += ["  " + p for p in live]
    lines += [
        "",
        "TAKEN PREFIXES (%d) -- do not claim one of these for a new router:" % len(r["taken_prefixes"]),
        "  " + ", ".join(sorted(r["taken_prefixes"])[:40]),
        "",
        "TAKEN TAGS -- reuse one of these rather than inventing a 60th:",
        "  " + ", ".join(list(r["taken_tags"])[:30]),
        "",
        "ALREADY SERVED, NOT LIVE (%d paths) -- these modules exist and are unmounted."
        % (r["route_total"] - r["live_route_total"]),
        "  Proposing another module for one of these paths is the FU-069 duplicate class.",
    ]
    if r["duplicate_paths"]:
        lines += ["", "PATHS ALREADY BUILT MORE THAN ONCE (%d) -- never propose these again:"
                  % len(r["duplicate_paths"])]
        lines += ["  %s <- %s" % (d["path"], ", ".join(d["modules"]))
                  for d in r["duplicate_paths"][:12]]
    if c["shell_calls_hitting_no_LIVE_route"]:
        lines += ["", "THE SHELL CALLS THESE AND NOTHING LIVE SERVES THEM (%d) -- highest-value work:"
                  % len(c["shell_calls_hitting_no_LIVE_route"])]
        lines += ["  " + u for u in c["shell_calls_hitting_no_LIVE_route"][:15]]
    lines += ["", "MOUNTED %d / ROUTERS %d -- %d unmounted, %d of them declare no prefix at all."
              % (m["mounted_count"], m["router_modules_total"],
                 m["unmounted_count"], len(m["no_prefix_declared"]))]

    out = "\n".join(lines)
    if len(out) > budget:
        out = out[:budget - 40].rsplit("\n", 1)[0] + "\n  ... (truncated to context budget)"
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    quiet = "--quiet" in argv
    out_path = DEFAULT_OUT
    if "--out" in argv:
        out_path = argv[argv.index("--out") + 1]

    kl = build_app_surface_kl()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(kl, fh, indent=1)

    if "--architect-block" in argv:
        print(render_for_architect(kl))
        return 0

    r, m, c = kl["routes"], kl["mounts"], kl["consumers"]
    if not quiet:
        print("\n=== app surface KL @ %s (%s) ===" % (ROOT, kl["meta"]["built_at_commit"]))
        print("  routers %d | mounted %d | unmounted %d | no-prefix %d"
              % (m["router_modules_total"], m["mounted_count"],
                 m["unmounted_count"], len(m["no_prefix_declared"])))
        print("  routes declared %d | LIVE %d | duplicate paths %d | prefix collisions %d"
              % (r["route_total"], r["live_route_total"],
                 len(r["duplicate_paths"]), len(r["prefix_collisions"])))
        print("  shell calls %d | calling nothing live %d | routes with no caller %d"
              % (c["shell_call_count"], len(c["shell_calls_hitting_no_LIVE_route"]),
                 len(c["routes_with_no_caller"])))

    if "--report" in argv:
        print("\n--- DUPLICATE ROUTES (FU-069: dedup keys on filename, not URL) ---")
        for d in r["duplicate_paths"][:30]:
            print("  %-46s  <- %s" % (d["path"], ", ".join(d["modules"])))
        if not r["duplicate_paths"]:
            print("  none")
        print("\n--- PREFIX COLLISIONS ---")
        for x in r["prefix_collisions"][:20]:
            print("  %-24s claimed by %d modules: %s"
                  % (x["prefix"], len(x["modules"]), ", ".join(x["modules"][:6])))
        if not r["prefix_collisions"]:
            print("  none")
        print("\n--- SHELL CALLS WITH NO LIVE ROUTE (the #1481 class) ---")
        for u in c["shell_calls_hitting_no_LIVE_route"][:20]:
            print("  " + u)
        if not c["shell_calls_hitting_no_LIVE_route"]:
            print("  none")

    print("\nwrote %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
