"""FU-102 guard: every active service's root module must be COPY'd into the image.

Root cause this prevents (live on prod v64, 2026-07-25 -> 2026-07-27):
app/main.py mounts every service listed under services/active/ at build time, but
the Dockerfile COPY-list is hand-maintained. When a builder lands a new service
and nobody updates the COPY-list, the module is simply absent from the image and
prod raises ModuleNotFoundError at mount time -- /spine/health goes ok:false with
a non-empty failures[] while CI stays green, because CI runs against the repo
tree (where the file exists), not the image.

False-positive guard: a services/active/<name>/ dir that has NO root <name>.py
(e.g. media_assets, which ships only a service.toml) is not a gap. Require the
root module to exist on disk before demanding a COPY for it.
"""

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKERFILE = os.path.join(REPO_ROOT, "Dockerfile")
ACTIVE_DIR = os.path.join(REPO_ROOT, "services", "active")


def _copy_source_tokens(dockerfile_text):
    """Yield the source paths of every COPY directive.

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
            yield src


def _copied_basenames(dockerfile_text):
    return {os.path.basename(src) for src in _copy_source_tokens(dockerfile_text)}


def _active_service_names():
    if not os.path.isdir(ACTIVE_DIR):
        return []
    return sorted(
        name
        for name in os.listdir(ACTIVE_DIR)
        if os.path.isdir(os.path.join(ACTIVE_DIR, name))
    )


def _read_dockerfile():
    with open(DOCKERFILE, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def test_dockerfile_copies_every_active_service_root_module():
    copied = _copied_basenames(_read_dockerfile())

    gaps = []
    for name in _active_service_names():
        root_module = name + ".py"
        if not os.path.exists(os.path.join(REPO_ROOT, root_module)):
            continue  # no root module to ship -- not a gap
        if root_module not in copied:
            gaps.append(root_module)

    assert not gaps, (
        "services/active modules missing from the Dockerfile COPY-list; these "
        "will ModuleNotFoundError on prod at mount time: " + ", ".join(gaps)
    )


def test_copy_list_has_no_dangling_root_modules():
    """COPY-list entries naming a root .py that no longer exists break the image
    build outright. Cheaper to catch here than at deploy time."""
    dangling = []
    for src in _copy_source_tokens(_read_dockerfile()):
        if src.endswith(".py") and "/" not in src and "*" not in src:
            if not os.path.exists(os.path.join(REPO_ROOT, src)):
                dangling.append(src)

    assert not dangling, (
        "Dockerfile COPY-list references root modules that do not exist: "
        + ", ".join(dangling)
    )
