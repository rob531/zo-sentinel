#!/usr/bin/env python3
"""seed_active_registry.py -- ONE-TIME Step-2 bootstrap of services/active/.

Reads the CURRENT live mount set from app/main.py's `_OPTIONAL_ROUTERS` list
and writes one `services/active/<name>/service.toml` per live router -- the
first real inhabitants of the SOA registry (design 2026-07-21, step 2).

This is a bootstrap, kept in the repo as the auditable record of HOW active/
was seeded. After it runs, services/active/ is the source of truth and this
script is not part of the loop -- generate_spine.py reads the tomls, main.py
runs the generated file, and new services arrive via services/staged/ ->
staged->active promotion.

It creates NO new router files and MOVES no code: each live router keeps its
existing module path; the toml just registers it (presence == registration) and
records the contract (import_path/prefix/tag/needs_data_layer/origin) derived
statically from the module source. Fully reversible: delete services/active/.

    python tools/seed_active_registry.py            # write the tomls
    python tools/seed_active_registry.py --dry-run  # print, write nothing
"""
from __future__ import annotations

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "app", "main.py")
ACTIVE = os.path.join(ROOT, "services", "active")

_APIROUTER_MARK = "API" + "Router("
PREFIX_DECL = re.compile(re.escape(_APIROUTER_MARK) + r"[^)]*prefix\s*=\s*[\"']([^\"']+)[\"']", re.S)
TAGS_DECL = re.compile(r"tags\s*=\s*\[([^\]]*)\]")
DATA_LAYER = re.compile(r"from app\.(db|models)|import app\.(db|models)")


def _read(p):
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def parse_optional_routers(src):
    """Extract the _OPTIONAL_ROUTERS list literal from app/main.py via AST."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_OPTIONAL_ROUTERS":
                    return [el.value for el in node.value.elts
                            if isinstance(el, ast.Constant) and isinstance(el.value, str)]
    return []


def _module_file(import_path):
    return os.path.join(ROOT, import_path.replace(".", os.sep) + ".py")


def describe(import_path):
    src = _read(_module_file(import_path))
    prefix = None
    pm = PREFIX_DECL.search(src)
    if pm:
        prefix = pm.group(1)
    tag = None
    tm = TAGS_DECL.search(src)
    if tm:
        tags = re.findall(r"[\"']([^\"']+)[\"']", tm.group(1))
        tag = tags[0] if tags else None
    return {
        "prefix": prefix,
        "tag": tag,
        "needs_data_layer": bool(DATA_LAYER.search(src)),
        "exists": os.path.isfile(_module_file(import_path)),
    }


def _toml(meta):
    def val(v):
        if v is None:
            return '""'
        if isinstance(v, bool):
            return "true" if v else "false"
        return '"%s"' % v
    lines = ["[service]"]
    for k in ("name", "import_path", "prefix", "tag", "origin", "auth"):
        lines.append("%s = %s" % (k, val(meta[k])))
    lines.append("needs_data_layer = %s" % ("true" if meta["needs_data_layer"] else "false"))
    lines.append("")
    lines.append("# Seeded from app/main.py _OPTIONAL_ROUTERS by tools/seed_active_registry.py.")
    lines.append("# Live pre-SOA router: code stays at its module path; this file registers it.")
    return "\n".join(lines) + "\n"


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    dry = "--dry-run" in argv
    names = parse_optional_routers(_read(MAIN))
    if not names:
        print("no _OPTIONAL_ROUTERS found in app/main.py -- nothing to seed")
        return 1
    written = 0
    for ip in names:
        stem = ip.split(".")[-1]
        d = describe(ip)
        meta = {
            "name": stem,
            "import_path": ip,
            "prefix": d["prefix"],
            "tag": d["tag"],
            "origin": "live",
            "auth": "public",
            "needs_data_layer": d["needs_data_layer"],
        }
        note = "" if d["exists"] else "  (WARNING: module file not found)"
        sdir = os.path.join(ACTIVE, stem)
        toml_path = os.path.join(sdir, "service.toml")
        print("  %-42s -> services/active/%s/service.toml  prefix=%s%s"
              % (ip, stem, d["prefix"] or "-", note))
        if not dry:
            os.makedirs(sdir, exist_ok=True)
            with open(toml_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(_toml(meta))
            written += 1
    print("\n%s %d service.toml files (%d live routers)"
          % ("would write" if dry else "wrote", len(names), len(names)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
