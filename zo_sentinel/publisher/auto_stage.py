"""auto_stage.py -- a builder artifact may not CREATE a service in active/.

THE FAILURE THIS ENDS

`tools/generate_spine.py --strict` is the top blocking failure on autonomous
build PRs. Measured 2026-09-13 on the real failing branch:

    STRICT: 1 UNLISTED broken active service(s): risk_axis_time_series=NO_TOML

The PR added `services/active/risk_axis_time_series/router.py` -- 469 lines --
and no `service.toml`, because the publisher writes exactly ONE file
(gitops.py: "the builder ... emits exactly one file"). A directory under
`services/active/` with no manifest is not a service; `--strict` is right to
refuse it, and no amount of re-running the PR can fix it.

Sampling the branches failing on 2026-09-13, roughly a third are this exact
shape: `scaffold_<name>_service_toml` branches writing
`services/active/<name>/router.py`.

WHY REDIRECT RATHER THAN DECLARE

The obvious alternatives are both worse:

  * Write the service.toml too. That MOUNTS the service -- generate_spine
    builds SPINE_MOUNTS from active/ entries with a valid manifest -- so the
    builder would be self-mounting unreviewed routes into prod. The lane guard
    forbids exactly that, and gitops.py's auto_declare comment says so: "the
    module_from_exemplar lane guard forbids self-mounting".
  * Add the name to spine_known_issues.json. That file is for debt INHERITED
    when active/ was seeded; appending to it daily turns a satisfiable-gate
    allowlist into the graveyard the deferred list already became (62 against a
    cap of 40, a standing reopen trigger).

The repo already has the right destination. `tools/service_decomposer.py`
writes to `services/staged/<name>/`, emits the manifest itself, and says of
promotion: "neither promotes (that is the promoter's gated call)." Staging is
where a built service belongs until `promote_staged_to_active` passes it. The
decomposer even writes `import_path = "services.active.<name>.router"` into the
staged manifest so promotion needs no rewrite.

So: send the artifact where the pipeline already expects it.

THE BOUNDARY THAT KEEPS THIS SAFE

Only CREATION is redirected. A directive that edits a service which already
exists in active/ is doing legitimate maintenance on a live service, and
redirecting that would silently fork a staged copy while leaving prod
untouched -- worse than the bug. The test for "already exists" is the manifest,
because that is what makes a directory a service to every other tool here.
"""
from __future__ import annotations

import os
import re
from typing import Optional, Tuple

ACTIVE_PREFIX = "services/active/"
STAGED_PREFIX = "services/staged/"


def _service_name(rel_path: str) -> Optional[str]:
    rest = rel_path[len(ACTIVE_PREFIX):]
    if not rest:
        return None
    name = rest.split("/", 1)[0]
    return name or None


def service_exists(clone_dir, name: str) -> bool:
    """A directory is a SERVICE when it carries the manifest every other tool
    reads. `services/active/<name>/` with only stray files is a directory the
    builder happened to create, not a service to edit."""
    return os.path.isfile(os.path.join(str(clone_dir), "services", "active",
                                       name, "service.toml"))


def redirect(clone_dir, rel_path: str) -> Tuple[str, Optional[str]]:
    """Return (path_to_write, reason_if_redirected).

    Creation of a NEW service under active/ is redirected to staged/.
    Everything else -- edits to an existing service, and every path outside
    active/ -- is returned unchanged.
    """
    rel_path = (rel_path or "").replace("\\", "/").lstrip("./")
    if not rel_path.startswith(ACTIVE_PREFIX):
        return rel_path, None

    name = _service_name(rel_path)
    if not name:
        return rel_path, None

    if service_exists(clone_dir, name):
        # Live service, real edit. Leave it alone.
        return rel_path, None

    staged = STAGED_PREFIX + rel_path[len(ACTIVE_PREFIX):]
    reason = (
        "services/active/%s has no service.toml, so this artifact would CREATE "
        "a service in active/ without a manifest -- generate_spine --strict "
        "fails that as %s=NO_TOML and the PR can never go green. The builder "
        "emits one file and cannot also write the manifest, and writing it "
        "would be self-mounting. Routed to services/staged/, where "
        "service_decomposer already puts built services and where "
        "promote_staged_to_active is the gated path to active/."
        % (name, name)
    )
    return staged, reason


# ---------------------------------------------------------------------------
# ROOT-LEVEL ROUTERS -> services/staged/<stem>/  (autopoietic_grant G09)
# ---------------------------------------------------------------------------
#
# THE FAILURE THIS ENDS
#
# A builder artifact that is a ROOT-level router module (`thing_api.py` with an
# APIRouter) used to land at the repo root and be auto-declared into
# tools/reachability_deferred.json (auto_declare.py). That made the ratchet
# satisfiable, but every one of those routers became a permanent orphan: the
# deferred list stood at 62 against the council's cap of 40, and nothing ever
# drains it because a root-level module has no manifest and no promotion path.
#
# The repo already has the path those routers need: services/staged/<name>/
# with a [service] manifest is exactly what service_decomposer emits and what
# promote_staged_to_active reads. So a NEW root-level router is written there
# instead, with its manifest, and the promoter -- not a human mount-lane review
# -- decides on evidence whether it reaches active/.
#
# THE BOUNDARY THAT KEEPS THIS SAFE (same rule as redirect() above)
#
#   * Only CREATION moves. If the root file already exists in the clone, the
#     artifact is an edit of a live module; moving it would fork a copy and
#     leave the original untouched. Unchanged -> auto_declare still applies.
#   * A router something in app/ already references is left where it is.
#   * A name that already exists as an ACTIVE service is left alone: staging it
#     would collide with prod at promotion time.
#   * Staged manifests are not mounted. generate_spine builds SPINE_MOUNTS from
#     services/active/ only, so writing the manifest here is not self-mounting.

STAGED_MANIFEST = "service.toml"
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PREFIX_RE = re.compile(r"APIRouter\s*\([^)]*prefix\s*=\s*[\"']([^\"']+)[\"']", re.S)


def render_manifest(name: str, content: str = "") -> str:
    """Canonical staged manifest -- the same shape tools/check_service_manifests.py
    render() and tools/service_decomposer.py emit, duplicated rather than imported
    because the publisher must not depend on tools/ being importable. The shape
    gate (run with --fix right after) is the check that the two never drift."""
    m = _PREFIX_RE.search(content or "")
    prefix = m.group(1) if (m and m.group(1).startswith("/")) else "/api"
    return (
        "[service]\n"
        'name = "%s"\n'
        'import_path = "services.active.%s.router"\n'
        'prefix = "%s"\n'
        'tag = "%s"\n'
        'origin = "service"\n'
        'auth = "public"\n'
        "needs_data_layer = true\n"
        "\n# Emitted by zo_sentinel/publisher/auto_stage.py for a root-level builder\n"
        "# router. import_path names services.ACTIVE because that is where router.py\n"
        "# lives AFTER promote_staged_to_active passes it.\n"
        % (name, name, prefix, name)
    )


def stage_root_router(clone_dir, rel_path: str, content: str):
    """Plan the staging of a NEW root-level router artifact.

    Returns (files, reason). `files` is an ordered list of (repo_rel_path, text)
    to write -- router first, then any missing manifest / package marker -- or
    None when the artifact is not redirected, in which case `reason` says why
    (and the caller keeps the old root-level + auto_declare behaviour).
    Never raises.
    """
    # Local import: auto_declare owns the ratchet-shape definitions and must
    # stay the single copy of them in the publisher.
    from . import auto_declare
    try:
        rel = (rel_path or "").replace("\\", "/").lstrip("./")
        if not auto_declare.is_router_module(rel, content):
            return None, "not a root-level router module"
        stem = os.path.basename(rel)[:-3]
        if not _IDENT_RE.match(stem):
            return None, "stem %r is not a service identifier" % stem
        root = str(clone_dir)
        if os.path.exists(os.path.join(root, rel)):
            return None, "root module already exists -- an edit, not a creation"
        if auto_declare.is_mounted(root, stem):
            return None, "already referenced from app/ -- left in place"
        if os.path.isdir(os.path.join(root, "services", "active", stem)):
            return None, "services/active/%s exists -- staging would collide" % stem

        base = STAGED_PREFIX + stem + "/"
        files = [(base + "router.py", content)]
        if not os.path.exists(os.path.join(root, "services", "staged", stem,
                                           STAGED_MANIFEST)):
            files.append((base + STAGED_MANIFEST, render_manifest(stem, content)))
        if not os.path.exists(os.path.join(root, "services", "staged", stem,
                                           "__init__.py")):
            files.append((base + "__init__.py", ""))
        reason = ("new root-level router %s routed to %s with a [service] manifest "
                  "instead of being auto-declared deferred; promote_staged_to_active "
                  "is its gated path to active/" % (rel, base))
        return files, reason
    except Exception as e:  # noqa: BLE001 -- never fail the publish over routing
        return None, "auto-stage skipped: %s" % e


def staged_companions(clone_dir, rel_path: str, content: str):
    """Missing manifest / package marker for a router landing in
    services/staged/<name>/router.py. Returns a list of (rel, text), possibly
    empty. A staged router with no manifest is an ORPHAN-DIR that
    check_service_manifests reports and promote_staged_to_active cannot read.
    Never raises."""
    try:
        rel = (rel_path or "").replace("\\", "/").lstrip("./")
        if not rel.startswith(STAGED_PREFIX) or not rel.endswith("/router.py"):
            return []
        parts = rel[len(STAGED_PREFIX):].split("/")
        if len(parts) != 2 or not _IDENT_RE.match(parts[0]):
            return []
        name = parts[0]
        d = os.path.join(str(clone_dir), "services", "staged", name)
        out = []
        if not os.path.exists(os.path.join(d, STAGED_MANIFEST)):
            out.append((STAGED_PREFIX + name + "/" + STAGED_MANIFEST,
                        render_manifest(name, content)))
        if not os.path.exists(os.path.join(d, "__init__.py")):
            out.append((STAGED_PREFIX + name + "/__init__.py", ""))
        return out
    except Exception:  # noqa: BLE001
        return []


def fix_manifests(clone_dir, manifests, timeout: int = 60):
    """Run the clone's own tools/check_service_manifests.py --fix on the given
    repo-relative manifests. Returns (ok, detail). ok=True also when the tool is
    absent from the clone (nothing to run -- CI is still the gate). Never raises.

    Subprocess, not import: the publisher must not depend on tools/ being
    importable, and running the real CLI is what proves the shapes agree."""
    import subprocess
    import sys
    root = str(clone_dir)
    tool = os.path.join(root, "tools", "check_service_manifests.py")
    if not os.path.isfile(tool):
        return True, "check_service_manifests.py not in clone -- skipped"
    try:
        r = subprocess.run(
            [sys.executable, tool, "--fix"]
            + [os.path.join(root, m.replace("/", os.sep)) for m in manifests],
            cwd=root, capture_output=True, text=True, timeout=timeout)
        tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        return r.returncode == 0, (tail[-1] if tail else "rc=%d" % r.returncode)
    except Exception as e:  # noqa: BLE001
        return False, "check_service_manifests --fix failed to run: %s" % e
