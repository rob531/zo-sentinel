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
