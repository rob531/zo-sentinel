#!/usr/bin/env python3
"""Repo-wide service-manifest shape gate.

WHY THIS EXISTS (FU-120, 2026-07-29)
------------------------------------
`pr_triage._manifest_is_valid()` already parsed manifests correctly -- but it was
wired to a CLASSIFIER, not a merge gate. `False` demoted a PR into the ordinary
scaffold bucket, which is a LABEL. Nothing refused the merge. So the validator was
described as "fail-closed" and 7 unparseable manifests landed on main anyway, the
most recent at 22:05 on 2026-07-29 (#2398).

"Contained" was a claim about the gate's POSITION, not its verdict.

This is the missing assertion: a BLOCKING, repo-wide, existential check that every
`services/*/<name>/service.toml` parses as TOML and carries the keys the promoter
blocks on. Pure stdlib, no host, no network, no app import.

WHAT IT CATCHES
---------------
  1. UNPARSEABLE  -- the file is not TOML. Observed shape: Python source, e.g.
                     `service = { "needs_data_layer": True }` plus a trailing
                     `if __name__ == "__main__": print("PASS")`. The builder was
                     writing a Python module into a .toml slot because every
                     neighbouring recipe step demanded py_compile and a PASS print.
  2. FLAT         -- valid TOML, but every key at top level with no [service]
                     table header. `promote_staged_to_active` does
                     `_load_toml(p).get("service", {})` -> `{}` -> the service is
                     unpromotable FOREVER, and reports as ordinary backlog.
  3. MISSING KEY  -- [service] present but name/import_path/prefix/tag empty.

Exit 0 = every manifest is promotable-shaped. Exit 1 = at least one is not.

USAGE
    python tools/check_service_manifests.py            # gate the whole repo
    python tools/check_service_manifests.py --fix      # reshape in place
    python tools/check_service_manifests.py path/...   # gate specific files
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import tomllib

# Aligned to the ACTUAL consumer, not to intuition.
#
# promote_staged_to_active blocks on exactly this:
#     if not meta.get("name") or not meta.get("import_path"): -> HOLD
# so those two must be non-empty.
#
# prefix/tag must merely be PRESENT. `prefix = ""` is legitimate and load-bearing
# for the nine `origin = "live"` pre-SOA routers seeded from app/main.py
# _OPTIONAL_ROUTERS -- their router objects carry their own prefix internally.
# An earlier draft of this gate demanded non-empty and would have "fixed" those
# nine into `prefix = "/api"`, silently MOVING NINE LIVE PROD ROUTES under the
# cover of a lint cleanup. A gate must encode the consumer's contract, not the
# author's guess about it.
REQUIRED_NONEMPTY = ("name", "import_path")
REQUIRED_PRESENT = ("prefix", "tag")
REQUIRED_KEYS = REQUIRED_NONEMPTY + REQUIRED_PRESENT
OPTIONAL_KEYS = ("origin", "auth", "needs_data_layer")
MANIFEST_GLOB = "services/*/*/service.toml"

# Values the builder emits as Python rather than TOML.
_PY_BOOL = {"True": "true", "False": "false"}

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
IMPORT_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def classify(path: str) -> tuple[str, str]:
    """Return (verdict, detail) for a manifest ON DISK."""
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError as exc:
        return "UNPARSEABLE", "unreadable: %s" % exc
    dirname = os.path.basename(os.path.dirname(os.path.abspath(path)))
    return classify_source(text, dirname)


def classify_source(text: str, dirname: str) -> tuple[str, str]:
    """Return (verdict, detail) for manifest CONTENT.

    Split out from classify() so tools/queue_census.py can judge a PR DIFF -- the
    thing that would land -- through exactly this code rather than a second copy of
    the rule. Two copies of a rule drift, and then one of them is wrong and nobody
    knows which. verdict in
    {OK, UNPARSEABLE, FLAT, MISSING_KEYS, BAD_NAME, NAME_MISMATCH, BAD_IMPORT_PATH}.
    """
    try:
        data = tomllib.loads(text)
    except ValueError as exc:
        return "UNPARSEABLE", str(exc)

    meta = data.get("service")
    if not isinstance(meta, dict):
        top = [k for k in REQUIRED_KEYS if k in data]
        return "FLAT", "top-level keys, no [service] header (found: %s)" % (
            ",".join(top) or "none"
        )

    missing = [k for k in REQUIRED_NONEMPTY if not str(meta.get(k) or "").strip()]
    missing += ["%s (key absent)" % k for k in REQUIRED_PRESENT if k not in meta]
    if missing:
        return "MISSING_KEYS", ",".join(missing)

    # Non-empty is NOT enough. The first cut of the repair harvested
    # `name = "Test Org"` out of a FastAPI seed row inside a manifest that had an
    # entire router pasted into it, producing the perfectly well-formed and
    # completely wrong `import_path = "services.active.Test Org.router"`. The gate
    # said OK because the string was non-empty.
    #
    # The DIRECTORY is the service's identity -- it is what the promoter iterates
    # and what generate_spine falls back to. A manifest that disagrees with its own
    # directory is not a manifest for that service.
    name = str(meta["name"]).strip()
    if not IDENT_RE.match(name):
        return "BAD_NAME", "%r is not a snake_case service identifier" % name
    if name != dirname:
        return "NAME_MISMATCH", "manifest says %r, directory says %r" % (name, dirname)

    ip = str(meta["import_path"]).strip()
    if not IMPORT_PATH_RE.match(ip):
        return "BAD_IMPORT_PATH", "%r is not a dotted module path" % ip
    return "OK", ""


def _harvest(path: str) -> dict:
    """Pull the builder's INTENDED key/values out of a malformed manifest.

    Deliberately regex-based: the file may not be TOML at all. We reshape what the
    builder meant rather than regenerating from assumption, so a non-default prefix
    or tag survives the repair.
    """
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return {}
    out = {}
    # The directory is authoritative for identity -- never harvest `name` out of
    # the file body. See the "Test Org" note in classify().
    for key in ("prefix", "tag", "origin", "auth", "needs_data_layer", "import_path"):
        # matches   key = "v"   |   key = v   |   "key": "v",   |   "key": v,
        m = re.search(
            r'["\']?\b%s\b["\']?\s*[:=]\s*(?:"([^"]*)"|\'([^\']*)\'|([A-Za-z0-9_.]+))'
            % re.escape(key),
            src,
        )
        if not m:
            continue
        val = next((g for g in m.groups() if g is not None), "").strip()
        if val:
            out[key] = _PY_BOOL.get(val, val)
    return out


def _sane(meta: dict, name: str) -> dict:
    """Drop harvested values that cannot be right, so the deterministic default
    wins instead. A malformed manifest may contain arbitrary Python, so anything
    scraped out of it is a SUSPECT, not a fact."""
    out = dict(meta)
    ip = str(out.get("import_path") or "")
    if not IMPORT_PATH_RE.match(ip) or name not in ip.split("."):
        out.pop("import_path", None)
    if not str(out.get("prefix") or "/").startswith("/"):
        out.pop("prefix", None)
    if not IDENT_RE.match(str(out.get("tag") or "x")):
        out.pop("tag", None)
    if str(out.get("needs_data_layer") or "true") not in ("true", "false"):
        out.pop("needs_data_layer", None)
    if str(out.get("auth") or "public") not in ("public", "authenticated", "internal"):
        out.pop("auth", None)
    return out


def render(meta: dict, name_hint: str) -> str:
    """Emit the canonical manifest. Same shape as
    tools/service_decomposer.py:_service_toml -- one template, one truth.

    `name_hint` is the DIRECTORY name and is authoritative; `meta` only supplies
    the surviving non-identity fields."""
    name = name_hint
    meta = _sane(meta, name)
    return (
        "[service]\n"
        'name = "%s"\n'
        'import_path = "%s"\n'
        'prefix = "%s"\n'
        'tag = "%s"\n'
        'origin = "%s"\n'
        'auth = "%s"\n'
        "needs_data_layer = %s\n"
        % (
            name,
            meta.get("import_path") or "services.active.%s.router" % name,
            meta.get("prefix") or "/api",
            meta.get("tag") or name,
            meta.get("origin") or "service",
            meta.get("auth") or "public",
            meta.get("needs_data_layer") or "true",
        )
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="service manifest shape gate")
    ap.add_argument("paths", nargs="*", help="manifests to check (default: whole repo)")
    ap.add_argument("--fix", action="store_true", help="reshape malformed manifests in place")
    args = ap.parse_args(argv)

    root = _repo_root()
    if args.paths:
        paths = [os.path.abspath(p) for p in args.paths]
    else:
        paths = sorted(glob.glob(os.path.join(root, MANIFEST_GLOB)))

    bad = []
    fixed = []
    for p in paths:
        verdict, detail = classify(p)
        if verdict == "OK":
            continue
        rel = os.path.relpath(p, root).replace(os.sep, "/")
        if args.fix:
            hint = os.path.basename(os.path.dirname(os.path.abspath(p)))
            if IDENT_RE.match(hint):
                # The directory name alone is enough to render a correct manifest,
                # so a repair never depends on scraping the broken file.
                with open(p, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(render(_harvest(p), hint))
                after, after_detail = classify(p)
                if after == "OK":
                    fixed.append(rel)
                    continue
                detail = "fix produced %s: %s" % (after, after_detail)
            else:
                detail = "directory name %r is not a service identifier" % hint
        bad.append((rel, verdict, detail))

    for rel in fixed:
        print("FIXED       %s" % rel)
    for rel, verdict, detail in bad:
        print("%-12s %s :: %s" % (verdict, rel, detail))

    total = len(paths)
    print(
        "\n%d manifest(s) checked | %d ok | %d fixed | %d BAD"
        % (total, total - len(bad) - len(fixed), len(fixed), len(bad))
    )
    if bad:
        print(
            "\nA manifest without a [service] table makes its service permanently\n"
            "unpromotable -- promote_staged_to_active reads .get('service', {}).\n"
            "Run `python tools/check_service_manifests.py --fix` to reshape."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
