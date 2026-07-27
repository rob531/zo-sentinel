"""The alembic revision graph must be shippable by `alembic upgrade head`.

WHY THIS EXISTS
---------------
`fly.toml` carries `release_command = "alembic upgrade head"`, so every prod
deploy runs this command against the prod moat Postgres. There is no true Fly
migration rollback, and a release has already failed on this path once (v61).

`prod-drift-sentinel` classifies each candidate's migration risk before staging
a deploy, and until now it did so by diffing migration FILE PATHS between prod's
commit and the candidate. That method has two holes:

  1. It rests on an *approximation* of prod's commit. Prod's /version reported
     `git_sha: "unknown"` for its entire life (#2063 added the build args; they
     only take effect on the next deploy), so "no migration files changed since
     prod" is inferred, not measured.
  2. A path diff is blind to the failure mode that actually breaks
     `alembic upgrade head`. Two branch heads in the revision graph -- which can
     arise from two PRs each adding a revision whose `down_revision` is the same
     parent, with NO file ever modified -- makes alembic abort with
     "Multiple head revisions are present". Nothing about the file list changes.

This module checks the graph alembic itself walks, so the verdict does not
depend on knowing prod's commit at all: a single-headed, fully-linked chain is
upgradeable from ANY point on it.

Stdlib only, no DB, no network, no alembic import required -- it reads the
revision identifiers straight out of the migration sources, so it runs in CI, on
the tower verifier, and inside a disposable deploy worktree identically.
"""

from __future__ import annotations

import ast
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSIONS_DIR = os.path.join(REPO_ROOT, "migrations", "versions")


def _string_or_none(node):
    """Return the str value of a literal node, or None for `None`/non-literals."""
    if isinstance(node, ast.Constant):
        if node.value is None or isinstance(node.value, str):
            return node.value
    return None


def _revision_ids(path):
    """Parse (revision, down_revision) out of one migration module.

    Parsed with `ast` rather than a regex or an import: a regex trips over
    `down_revision: str | None = "0009_x"` annotations and over the same names
    appearing in docstrings, and importing the module would execute it.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        tree = ast.parse(fh.read(), filename=path)

    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        else:
            continue
        value = node.value
        for name in targets:
            if name in ("revision", "down_revision") and name not in found:
                found[name] = _string_or_none(value)
    return found.get("revision"), found.get("down_revision")


def _load_graph():
    files = sorted(
        os.path.join(VERSIONS_DIR, n)
        for n in os.listdir(VERSIONS_DIR)
        if n.endswith(".py") and not n.startswith("__")
    )
    graph = {}
    for path in files:
        rev, down = _revision_ids(path)
        graph[path] = (rev, down)
    return graph


@pytest.fixture(scope="module")
def graph():
    if not os.path.isdir(VERSIONS_DIR):
        pytest.fail("migrations/versions/ is missing: the release_command cannot run")
    loaded = _load_graph()
    if not loaded:
        pytest.fail("no migration revisions found under migrations/versions/")
    return loaded


def test_every_migration_declares_a_revision_id(graph):
    """A file alembic cannot read a `revision` from is not part of the chain."""
    missing = sorted(
        os.path.basename(p) for p, (rev, _) in graph.items() if not rev
    )
    assert not missing, (
        "migration files with no parseable `revision` identifier: {}. alembic "
        "will not treat these as revisions, so whatever they create never "
        "reaches prod.".format(missing)
    )


def test_revision_ids_are_unique(graph):
    """Two files claiming one id makes `upgrade head` non-deterministic."""
    seen = {}
    dupes = {}
    for path, (rev, _) in graph.items():
        if not rev:
            continue
        if rev in seen:
            dupes.setdefault(rev, [os.path.basename(seen[rev])]).append(
                os.path.basename(path)
            )
        else:
            seen[rev] = path
    assert not dupes, "duplicate revision identifiers: {}".format(dupes)


def test_every_down_revision_resolves(graph):
    """A dangling parent breaks the walk with 'Can't locate revision'."""
    known = {rev for rev, _ in graph.values() if rev}
    dangling = {}
    for path, (rev, down) in graph.items():
        if down and down not in known:
            dangling[os.path.basename(path)] = down
    assert not dangling, (
        "down_revision pointing at a revision that does not exist: {}. "
        "`alembic upgrade head` aborts with \"Can't locate revision\".".format(dangling)
    )


def test_exactly_one_base_revision(graph):
    """More than one `down_revision = None` is a second, disconnected chain."""
    bases = sorted(
        rev for rev, down in graph.values() if rev and down is None
    )
    assert len(bases) == 1, (
        "expected exactly 1 base revision (down_revision = None), found {}: {}. "
        "Multiple bases mean the chain is not connected and part of it will "
        "never be applied.".format(len(bases), bases)
    )


def test_exactly_one_head_revision(graph):
    """THE gate. Two heads make the prod release_command fail outright.

    `alembic upgrade head` raises "Multiple head revisions are present" and the
    Fly release aborts -- against a Postgres with no migration rollback. This is
    reachable with NO file ever modified: two PRs each adding a new revision on
    the same parent produces it on merge, and every per-file check stays green.
    """
    revisions = {rev for rev, _ in graph.values() if rev}
    parents = {down for _, down in graph.values() if down}
    heads = sorted(revisions - parents)
    assert len(heads) == 1, (
        "expected exactly 1 head revision, found {}: {}. `alembic upgrade head` "
        "-- the fly.toml release_command -- fails with \"Multiple head revisions "
        "are present\" and the prod release aborts. Merge the branches with a "
        "revision whose down_revision is a tuple of both heads.".format(
            len(heads), heads
        )
    )


def test_chain_reaches_every_revision_from_the_head(graph):
    """Walk head -> base. Anything unvisited is unreachable, or a cycle exists."""
    parent_of = {rev: down for rev, down in graph.values() if rev}
    revisions = set(parent_of)
    heads = sorted(revisions - {d for d in parent_of.values() if d})
    if len(heads) != 1:
        pytest.skip("single-head assertion owns this failure")

    walked = []
    seen = set()
    cursor = heads[0]
    while cursor is not None:
        if cursor in seen:
            pytest.fail("cycle in the revision graph at {}: {}".format(cursor, walked))
        seen.add(cursor)
        walked.append(cursor)
        cursor = parent_of.get(cursor)

    unreachable = sorted(revisions - seen)
    assert not unreachable, (
        "revisions not reachable by walking down from the head: {}. They are in "
        "the tree but `alembic upgrade head` will never apply them.".format(
            unreachable
        )
    )
