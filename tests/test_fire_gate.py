"""Tests for tools/fire_gate.py.

Deliberately built on the REAL Dockerfile shape of this repo (multi-operand COPY of
root modules, a backslash continuation, directory COPYs) rather than a toy one-liner --
the continuation form is the case that would silently drop ~17 modules from the surface
and turn a RESTAGE into a false SAFE.

Every assertion here has been seen to FAIL against a deliberately wrong implementation;
an assertion never seen red is not evidence.
"""

import importlib.util
import pathlib
import sys

import pytest

_MOD_PATH = pathlib.Path(__file__).resolve().parents[1] / "tools" / "fire_gate.py"
_spec = importlib.util.spec_from_file_location("fire_gate", _MOD_PATH)
fire_gate = importlib.util.module_from_spec(_spec)
sys.modules["fire_gate"] = fire_gate
_spec.loader.exec_module(fire_gate)


REAL_SHAPE = """\
FROM python:3.11-slim AS base
WORKDIR /srv
COPY app/requirements.txt /srv/app/requirements.txt
RUN pip install -r /srv/app/requirements.txt
COPY app /srv/app
COPY verdict_breakdown_api.py server_compare_api.py trust_gating_override.py /srv/
COPY facet_enum_service.py perspective_model.py \\
     ask_answer_api.py vuln_identity.py /srv/
COPY zo_sentinel/__init__.py zo_sentinel/policy.py zo_sentinel/policy_defaults.toml /srv/zo_sentinel/
COPY perspective_tree_view.html scan_view.html /srv/
COPY migrations /srv/migrations
COPY alembic.ini /srv/alembic.ini
COPY --from=frontend /build/dist /srv/app/static
CMD ["uvicorn", "app.main:app"]
"""


@pytest.fixture()
def surface():
    srcs = fire_gate.copy_sources(REAL_SHAPE)
    return srcs, *fire_gate.build_surface(srcs)


def test_backslash_continuation_is_spliced_not_dropped(surface):
    srcs, _, _ = surface
    # vuln_identity.py sits AFTER the continuation; a naive line-wise parser loses it.
    assert "vuln_identity.py" in srcs
    assert "ask_answer_api.py" in srcs
    assert "facet_enum_service.py" in srcs


def test_destination_operand_is_never_treated_as_a_source(surface):
    srcs, files, prefixes = surface
    assert "/srv/" not in srcs and "/srv/migrations" not in srcs
    assert not any(f.startswith("srv") for f in files)


def test_from_stage_copies_are_ignored(surface):
    srcs, _, _ = surface
    # inputs come from a build stage, not the git tree, so no repo path can change them
    assert not any("/build/dist" in s for s in srcs)


def test_directory_copies_become_prefixes(surface):
    _, files, prefixes = surface
    assert "app/" in prefixes
    assert "migrations/" in prefixes
    assert "alembic.ini" in files


@pytest.mark.parametrize(
    "path",
    [
        "app/main.py",                      # COPYed dir
        "migrations/versions/0011_x.py",    # COPYed dir
        "vuln_identity.py",                 # COPYed file behind a continuation
        "zo_sentinel/policy.py",            # COPYed file, nested
        "alembic.ini",                      # COPYed file
        "Dockerfile",                       # defines the COPY list
        ".dockerignore",                    # silently subtracts from every COPY
        "fly.toml",                         # release_command / health checks
        "services/active/foo.toml",         # promotion surface (FU-102 / v64 class)
    ],
)
def test_image_surface_paths_force_restage(path, surface):
    _, files, prefixes = surface
    assert fire_gate.classify(path, files, prefixes) is not None, path


@pytest.mark.parametrize(
    "path",
    [
        "tools/run_verify.ps1",
        "tools/fire_gate.py",
        ".github/workflows/copilot-autofix-commit.yml",
        "tests/test_fire_gate.py",
        "docs/AUTOPOIESIS.md",
        "services/staged/scaffold_thing/__init__.py",
    ],
)
def test_paths_that_cannot_reach_the_image_stay_safe(path, surface):
    _, files, prefixes = surface
    assert fire_gate.classify(path, files, prefixes) is None, path


def test_staged_beats_active_prefix_ordering(surface):
    """services/staged/ must stay inert even though services/active/ is sensitive."""
    _, files, prefixes = surface
    assert fire_gate.classify("services/staged/x/y.py", files, prefixes) is None
    assert fire_gate.classify("services/active/x.toml", files, prefixes) is not None


def test_copy_dot_makes_the_whole_tree_the_surface():
    srcs = fire_gate.copy_sources("FROM x\nCOPY . /srv/\n")
    files, prefixes = fire_gate.build_surface(srcs)
    assert "" in prefixes
    assert fire_gate.classify("anything/at/all.py", files, prefixes) is not None
    # ...but the staged scratch surface is still explicitly inert
    assert fire_gate.classify("services/staged/x.py", files, prefixes) is None


def test_zero_copy_sources_is_an_error_not_a_pass():
    """A Dockerfile we failed to parse must never yield an empty (=permissive) surface."""
    assert fire_gate.copy_sources("FROM python:3.11\nRUN echo hi\n") == []
