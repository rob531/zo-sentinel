"""Will this service's declared import_path actually EXIST inside the prod image?

WHY THIS MODULE EXISTS
----------------------
The FU-102 guard (tests/test_dockerfile_copy_covers_active_services.py) was
written after prod v64 shipped 7 services that raised ModuleNotFoundError at
mount time because their root modules were never added to the hand-maintained
Dockerfile COPY-list. That guard asked:

    does <service-dir-name>.py exist at the repo root, and is it COPY'd?

That question is a PROXY for the real one. The spine does not import a directory
name. `tools/generate_spine.py` copies `[service].import_path` VERBATIM out of
service.toml into SPINE_MOUNTS, and app/_spine_generated.py calls
`importlib.import_module(import_path)`. So the only thing that matters is:

    is the file that `import_path` resolves to present inside the image?

The proxy and the real question agree for every service that happens to be a
root module named after its directory -- which is every ACTIVE service today,
which is why the proxy has never been caught being wrong. They disagree for a
DOTTED import_path:

  * `media_assets` declares `app.routers.media_assets`. There is no root
    `media_assets.py`, so the proxy SKIPS it as "nothing to ship" -- and gets the
    right answer by luck, because `COPY app /srv/app` ships the whole package.

  * Every service under services/staged/ declares
    `services.active.<name>.router`. `tools/promote_staged_to_active.py` MOVES
    the folder without rewriting import_path, so a promoted service is imported
    as `services.active.<name>.router` -- and the Dockerfile COPYs no
    `services/` path at all. The proxy skips these too (no root <name>.py), so
    the guard built to prevent the v64 class stays GREEN while queueing up a
    repeat of it.

So: resolve the declared import_path to a file, then ask whether any COPY source
token is that file or an ancestor directory of it. That is the question the
image actually answers at mount time.

STATE OF THE WORLD, 2026-08-01 (FU-217) -- read this before trusting the
paragraph above. The second bullet describes the Dockerfile as it was until
2026-08-01. It is kept because it is why this module exists, but it is NO
LONGER a description of the shipped Dockerfile, and a docstring whose stated
method has drifted from the running one is its own defect class. What changed:

    COPY services/__init__.py /srv/services/
    COPY services/active     /srv/services/active

so `services.active.<name>.router` is now COVERED and `would_be_shipped`
returns True for it. This module's LOGIC is unchanged -- not one line of it was
touched -- because it was never the thing that was wrong: it correctly reported
a real gap, for four days, in a Dockerfile that had no COPY-list for services
at all (267 of 300 promoter candidates held). What changed is the Dockerfile it
reads. `services/staged` remains uncopied ON PURPOSE and this module will still
answer False for it; that asymmetry is asserted two-sidedly in
tests/test_dockerfile_copy_covers_active_services.py, so it cannot decay into
a bare `COPY services` unnoticed.

$0, static, no image build, no network.
"""

from __future__ import annotations

import os
import re

try:  # py3.11+
    import tomllib
except ImportError:  # pragma: no cover - py3.10 fallback
    import tomli as tomllib  # type: ignore


def copy_source_tokens(dockerfile_text):
    """Yield the source paths of every COPY directive, posix-normalised.

    Dockerfile line continuations ("\\" at EOL) are joined first, otherwise a
    multi-line COPY block parses as several bogus one-token directives.
    """
    text = re.sub(r"\\\s*\n", " ", dockerfile_text)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("COPY"):
            continue
        tokens = [t for t in stripped.split()[1:] if not t.startswith("--")]
        if len(tokens) < 2:
            continue
        for src in tokens[:-1]:  # last token is the destination
            src = src.replace("\\", "/").lstrip("./").rstrip("/")
            if src:
                yield src


def module_candidates(import_path):
    """Repo-relative posix paths an import_path could resolve to.

    A dotted path may be a module (`a/b/c.py`) or a package (`a/b/c/__init__.py`).
    """
    base = import_path.replace(".", "/")
    return [base + ".py", base + "/__init__.py"]


def resolve_module_path(repo_root, import_path):
    """The repo-relative posix path import_path resolves to, or None if absent."""
    for rel in module_candidates(import_path):
        if os.path.exists(os.path.join(repo_root, rel.replace("/", os.sep))):
            return rel
    return None


def is_copy_covered(rel_path, copy_tokens):
    """True iff a COPY source token is rel_path itself or an ancestor directory.

    `COPY app /srv/app` covers `app/routers/media_assets.py`; nothing covers
    `services/active/x/router.py` unless a token names `services` or deeper.
    """
    tokens = set(copy_tokens)
    parts = rel_path.split("/")
    for i in range(1, len(parts) + 1):
        if "/".join(parts[:i]) in tokens:
            return True
    return False


def read_import_path(service_toml_path):
    """[service].import_path from a service.toml, or None.

    Tolerates the second service.toml shape the builder emits (no [service]
    table) rather than exploding on it -- that is a different gate's finding.
    """
    try:
        with open(service_toml_path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError):
        return None
    service = data.get("service")
    if not isinstance(service, dict):
        return None
    ip = service.get("import_path")
    return ip if isinstance(ip, str) and ip else None


def would_be_shipped(import_path, dockerfile_text):
    """Coverage check that does NOT require the module to exist yet.

    Used at PROMOTION time. A staged service's files still live under
    services/staged/<name>/ while its import_path already names the post-move
    location services.active.<name>.router, so an existence-based check
    (shippability) returns NO_SOURCE and would fire on nothing -- the same
    proxy-instead-of-the-real-question mistake this module was written to fix,
    one layer up. Ask instead: would ANY path this import_path can resolve to be
    carried into the image?
    """
    tokens = list(copy_source_tokens(dockerfile_text))
    return any(is_copy_covered(rel, tokens) for rel in module_candidates(import_path))


def shippability(repo_root, import_path, dockerfile_text):
    """Classify one import_path against the Dockerfile.

    Returns (verdict, detail) where verdict is one of:
      "SHIPPED"     -- resolves to a file covered by a COPY directive
      "NOT_SHIPPED" -- resolves to a real file that NO COPY directive carries;
                       this is a guaranteed ModuleNotFoundError in prod
      "NO_SOURCE"   -- import_path resolves to no file in the repo at all; a
                       different problem, not this gate's to judge
    """
    rel = resolve_module_path(repo_root, import_path)
    if rel is None:
        return "NO_SOURCE", "%s resolves to no file in the repo" % import_path
    tokens = list(copy_source_tokens(dockerfile_text))
    if is_copy_covered(rel, tokens):
        return "SHIPPED", rel
    return "NOT_SHIPPED", (
        "%s -> %s, which no Dockerfile COPY directive carries into the image"
        % (import_path, rel)
    )
