#!/usr/bin/env python3
"""service_decomposer.py -- decompose a SERVICE into single-file directives.

The deterministic, proven bridge for SOA step 3 (the engine-per-file path from
the 2026-07-19 retrospective): rather than one multi-file directive (the shape
that produced 142 hollow PRs), a service is emitted as N SINGLE-FILE directives,
one per concern, each handled by the existing module_from_exemplar/engine lane
against the matching file of the canonical service-dir exemplar
(services/_exemplar/). Each emission stays in-lane; the SERVICE is the unit.

This is the safe twin of goose_recipes/service_dir_from_exemplar.yaml: the recipe
is the goose-native (eventually subrecipe) path; this is the deterministic
decomposition that needs no agent to PLAN the split. Both drop into
services/staged/<name>/; neither promotes (that is the promoter's gated call).

It EMITS DIRECTIVES only -- it writes no service code and touches no spine. The
directive shape matches the promoter's validator (task/handler/output_file/
description; handler in generate_file|write_raw|run_script).

    python tools/service_decomposer.py --name risk_delta --spec "GET /api/risk/delta ..."   # dry-run
    python tools/service_decomposer.py --name risk_delta --spec-file spec.txt --emit directives/proposed
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# concern -> (exemplar file, one-line role used in the directive description)
CODE_FILES = [
    ("logic.py", "data/computation over the REAL app.db/app.models (no inline/stub models)"),
    ("router.py", "thin APIRouter exposing `router`, RELATIVE `from .logic import ...`, Depends(get_session)"),
    ("contract.py", "acceptance self-test runnable as `python -m services.staged.{name}.contract`, TestClient + dependency_overrides + SQLite StaticPool, prints PASS/exit 0"),
]


def _init_body() -> str:
    return "# Auto-emitted service package. Relative intra-service imports survive\n" \
           "# staged->active promotion without rewrite.\n"


def _service_toml(name: str, prefix: str, tag: str) -> str:
    return (
        "[service]\n"
        'name = "%s"\n'
        'import_path = "services.active.%s.router"\n'
        'prefix = "%s"\n'
        'tag = "%s"\n'
        'origin = "service"\n'
        'auth = "public"\n'
        "needs_data_layer = true\n"
        "\n# Emitted by tools/service_decomposer.py. import_path names services.ACTIVE\n"
        "# because that is where router.py lives AFTER promotion.\n"
    ) % (name, name, prefix, tag)


def decompose(name: str, spec: str, prefix: str = "/api", tag: str = "",
              exemplar_dir: str = "services/_exemplar") -> list[dict]:
    """Return the ordered list of directive dicts for one service."""
    tag = tag or name
    staged = "services/staged/%s" % name
    directives: list[dict] = []

    # deterministic files first (write_raw): __init__.py + service.toml
    directives.append({
        "task": "scaffold_%s_init" % name,
        "handler": "write_raw",
        "output_file": "%s/__init__.py" % staged,
        "content": _init_body(),
        "description": ("Create the package __init__.py for service '%s' so its "
                        "relative intra-service imports work as a package under "
                        "services.staged/active. Deterministic scaffold." % name),
        "complexity": "low",
    })
    directives.append({
        "task": "scaffold_%s_service_toml" % name,
        "handler": "write_raw",
        "output_file": "%s/service.toml" % staged,
        "content": _service_toml(name, prefix, tag),
        "description": ("Create the service.toml registration for service '%s' "
                        "(import_path services.active.%s.router, prefix %s). This is "
                        "the contract the spine reads after promotion." % (name, name, prefix)),
        "complexity": "low",
    })

    # code files (generate_file, engine/exemplar-mirrored, one per concern)
    for fname, role in CODE_FILES:
        stem = fname[:-3]
        exemplar_file = "%s/%s" % (exemplar_dir, fname)
        directives.append({
            "task": "build_%s_%s" % (name, stem),
            "handler": "generate_file",
            "recipe": "service_dir_from_exemplar",
            "output_file": "%s/%s" % (staged, fname),
            "exemplar_file": exemplar_file,
            "reads": [exemplar_file],
            "description": (
                "Build services/staged/%s/%s for service '%s' by MIRRORING %s. "
                "Role: %s. Single-file only; import the REAL data layer; no stub DB. "
                "Service spec: %s"
                % (name, fname, name, exemplar_file, role.format(name=name), spec)
            ),
            "complexity": "medium",
        })
    return directives


def main(argv=None):
    ap = argparse.ArgumentParser(description="Decompose a service into single-file directives.")
    ap.add_argument("--name", required=True, help="snake_case service name")
    ap.add_argument("--spec", help="service contract text")
    ap.add_argument("--spec-file", help="read the spec from a file")
    ap.add_argument("--prefix", default="/api", help="route prefix (default /api)")
    ap.add_argument("--tag", default="", help="OpenAPI tag (default: name)")
    ap.add_argument("--exemplar-dir", default="services/_exemplar")
    ap.add_argument("--emit", metavar="DIR", help="write directive JSONs into DIR (else dry-run)")
    args = ap.parse_args(argv)

    spec = args.spec or (open(args.spec_file, encoding="utf-8").read() if args.spec_file else "")
    if not spec.strip():
        ap.error("provide --spec or --spec-file")

    directives = decompose(args.name, spec.strip(), args.prefix, args.tag, args.exemplar_dir)

    if not args.emit:
        print("# dry-run: %d directives for service '%s' (pass --emit DIR to write)"
              % (len(directives), args.name))
        for d in directives:
            print("  %-10s -> %s" % (d["handler"], d["output_file"]))
        return 0

    os.makedirs(args.emit, exist_ok=True)
    for d in directives:
        stem = d["output_file"].replace("/", "_").replace(".", "_")
        path = os.path.join(args.emit, "svc_%s.json" % stem)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2)
        print("wrote %s" % path)
    print("emitted %d directives for service '%s' into %s" % (len(directives), args.name, args.emit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
