"""Tests for zo_sentinel.undeclared_write_guard (GH #3415 prevention fix 4).

Environment-independent: builds a throwaway git repo per test. Stdlib + pytest
only, so it belongs in the evaluator.yml allowlist (a test not collected there
gates nothing -- FU: the 33-file allowlist).

The founding case (test_marker_write_during_build_is_reverted) replays the
2026-08-22 incident: a build-class directive with a declared output under
services/staged/ writes zo_sentinel/__init__.py instead. Before this module
existed, that write was invisible to every gate -- these tests were RED by
construction until sweep() was implemented.
"""
import subprocess
from pathlib import Path

import pytest

from zo_sentinel.undeclared_write_guard import (
    FORENSICS_DIRNAME, LOAD_BEARING_MARKERS, sweep, tracked_dirty)


def _mkrepo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*a):
        subprocess.run(["git", *a], cwd=str(repo), check=True,
                       capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "zo_sentinel").mkdir()
    (repo / "app").mkdir()
    (repo / "zo_sentinel" / "__init__.py").write_text("# bare marker\n")
    (repo / "app" / "__init__.py").write_text("# bare marker\n")
    (repo / "tracked_other.py").write_text("ORIGINAL = 1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    return repo


def test_marker_write_during_build_is_reverted(tmp_path):
    """Founding case: build-class directive mutates the package marker."""
    repo = _mkrepo(tmp_path)
    before = tracked_dirty(repo)
    assert before == set()
    marker = repo / "zo_sentinel" / "__init__.py"
    marker.write_text("# Auto-emitted service package\nfrom .models import X\n")
    acts = sweep(repo, "scaffold_contract_init", before,
                 declared_relpath="services/staged/contract/__init__.py")
    assert [a["file"] for a in acts] == ["zo_sentinel/__init__.py"]
    assert acts[0]["restored"] is True
    assert marker.read_text() == "# bare marker\n"          # live state, not intent
    forensic = Path(acts[0]["forensics"])
    assert forensic.is_file() and "from .models" in forensic.read_text()
    assert str(forensic).startswith(str(repo / FORENSICS_DIRNAME))


def test_build_class_reverts_any_tracked_write(tmp_path):
    repo = _mkrepo(tmp_path)
    before = tracked_dirty(repo)
    (repo / "tracked_other.py").write_text("MUTATED = 2\n")
    acts = sweep(repo, "d1", before, declared_relpath="services/staged/x/__init__.py")
    assert [a["file"] for a in acts] == ["tracked_other.py"]
    assert (repo / "tracked_other.py").read_text() == "ORIGINAL = 1\n"


def test_edit_class_tracked_write_is_kept(tmp_path):
    """Edit-class directives (declared None) legitimately modify tracked files."""
    repo = _mkrepo(tmp_path)
    before = tracked_dirty(repo)
    (repo / "tracked_other.py").write_text("LEGIT_EDIT = 3\n")
    acts = sweep(repo, "wire_x", before, declared_relpath=None)
    assert acts == []
    assert (repo / "tracked_other.py").read_text() == "LEGIT_EDIT = 3\n"


def test_marker_is_gated_even_for_edit_class(tmp_path):
    repo = _mkrepo(tmp_path)
    before = tracked_dirty(repo)
    (repo / "app" / "__init__.py").write_text("from app.routers import api_router\n")
    acts = sweep(repo, "wire_x", before, declared_relpath=None)
    assert [a["file"] for a in acts] == ["app/__init__.py"]
    assert (repo / "app" / "__init__.py").read_text() == "# bare marker\n"


def test_preexisting_dirt_is_never_touched(tmp_path):
    """Attribution before action: dirt present before the bracket is not ours."""
    repo = _mkrepo(tmp_path)
    (repo / "tracked_other.py").write_text("SOMEONE_ELSES = 4\n")
    before = tracked_dirty(repo)
    assert before == {"tracked_other.py"}
    acts = sweep(repo, "d1", before, declared_relpath="services/staged/x/y.py")
    assert acts == []
    assert (repo / "tracked_other.py").read_text() == "SOMEONE_ELSES = 4\n"


def test_declared_tracked_output_is_kept(tmp_path):
    """The declared path itself is exempt even when tracked."""
    repo = _mkrepo(tmp_path)
    before = tracked_dirty(repo)
    (repo / "tracked_other.py").write_text("DECLARED = 5\n")
    acts = sweep(repo, "d1", before, declared_relpath="tracked_other.py")
    assert acts == []


def test_no_attribution_basis_is_a_noop(tmp_path):
    """dirty_before=None means git could not answer BEFORE the run: no basis,
    no action (unknown is not zero, R6)."""
    repo = _mkrepo(tmp_path)
    (repo / "zo_sentinel" / "__init__.py").write_text("broken\n")
    acts = sweep(repo, "d1", None, declared_relpath="services/staged/x/y.py")
    assert acts == []
    assert (repo / "zo_sentinel" / "__init__.py").read_text() == "broken\n"


def test_not_a_git_repo_fails_open(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "f.py").write_text("x\n")
    assert tracked_dirty(plain) is None
    assert sweep(plain, "d1", set(), declared_relpath="a/b.py") == []


def test_untracked_new_files_are_ignored(tmp_path):
    """A build's NEW file (its normal product) is untracked -> never in scope."""
    repo = _mkrepo(tmp_path)
    before = tracked_dirty(repo)
    staged = repo / "services" / "staged" / "contract"
    staged.mkdir(parents=True)
    (staged / "__init__.py").write_text("# new service\n")
    acts = sweep(repo, "d1", before, declared_relpath="services/staged/contract/__init__.py")
    assert acts == []
    assert (staged / "__init__.py").is_file()


def test_markers_constant_matches_incident_files():
    assert LOAD_BEARING_MARKERS == {"zo_sentinel/__init__.py", "app/__init__.py"}
