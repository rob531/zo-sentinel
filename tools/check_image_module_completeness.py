#!/usr/bin/env python3
"""check_image_module_completeness.py -- fail if a root module that MUST ship is
not COPY'd into the Docker image.

Root cause this guards against (zo-sentinel #12366): the Dockerfile COPYs root-level
modules EXPLICITLY, file by file (there is no `COPY . .` for the repo root). When a
new root module is added and nobody edits the Dockerfile, it is simply absent from
the image, and every `from <module> import ...` raises ModuleNotFoundError at spine
mount / app startup. This kept the whole `unauth_access` signal dark (#12256) until
#12312 added its COPY line.

Rule enforced: for every root module that is (a) a SPINE_MOUNTS import_path, or
(b) imported at module scope by such a module, that root module MUST appear on a
Dockerfile COPY line. Modules only referenced by app/main.py's OPTIONAL try/except
daemons (e.g. attestation_refresher, which is zocompute-only) are NOT required --
this guard is about modules the mounted API surface depends on.

Exit 0 = all required root modules ship. Exit 1 = a gap (prints the offenders).
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
SPINE = ROOT / "app" / "_spine_generated.py"


def copied_root_modules() -> set[str]:
    """Root .py module names (sans .py) that a Dockerfile COPY places at /srv/.

    Joins backslash line-continuations first -- many COPY statements span several
    physical lines, and the modules on the continuation lines ship just the same."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    logical = re.sub(r"\\\s*\n", " ", text)        # fold `...\<newline>` into one line
    shipped: set[str] = set()
    for line in logical.splitlines():
        s = line.strip()
        if not s.startswith("COPY "):
            continue
        for tok in s[len("COPY "):].split():
            if tok.endswith(".py") and "/" not in tok:
                shipped.add(tok[:-3])
    return shipped


def spine_import_paths() -> list[str]:
    """import_path values from app/_spine_generated.py SPINE_MOUNTS (root-level only)."""
    paths = []
    for m in re.finditer(r"'import_path'\s*:\s*'([^']+)'", SPINE.read_text(encoding="utf-8")):
        ip = m.group(1)
        if "." not in ip:            # a root module (e.g. 'server_risk_axes_api'), not 'services.x.y'
            paths.append(ip)
    return paths


def root_imports_of(module: str) -> set[str]:
    """Root sibling modules imported at module scope by ROOT/<module>.py."""
    f = ROOT / f"{module}.py"
    if not f.exists():
        return set()
    out: set[str] = set()
    try:
        tree = ast.parse(f.read_text(encoding="utf-8"))
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        for n in names:
            top = n.split(".")[0]
            if (ROOT / f"{top}.py").exists():   # it's a root sibling module
                out.add(top)
    return out


def main() -> int:
    shipped = copied_root_modules()
    required: set[str] = set()
    for ip in spine_import_paths():
        if (ROOT / f"{ip}.py").exists():
            required.add(ip)
            required |= root_imports_of(ip)   # one hop is enough to catch the #12256/#12312 class
    missing = sorted(m for m in required if m not in shipped)
    if missing:
        print("FAIL: root modules required by the mounted API surface but NOT COPY'd "
              "into the image (add a `COPY <name>.py /srv/` line to the Dockerfile):")
        for m in missing:
            print(f"  - {m}.py")
        return 1
    print(f"OK: all {len(required)} mount-required root modules are COPY'd into the image.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
