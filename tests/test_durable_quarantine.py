"""Durable quarantine: a .failed sentinel in EITHER the in-repo directives/ path
OR a store outside the git tree parks a directive -- so `git clean` on respawn
can't un-quarantine it (kills the re-flush treadmill). Council 2026-06-20."""
from zo_sentinel.build_completion import failed_quarantined


def test_none_when_absent(tmp_path):
    a = tmp_path / "repo"; b = tmp_path / "durable"
    a.mkdir(); b.mkdir()
    assert failed_quarantined("build_x", a, b) is False


def test_found_in_repo_dir(tmp_path):
    a = tmp_path / "repo"; b = tmp_path / "durable"
    a.mkdir(); b.mkdir()
    (a / "build_x.failed.json").write_text("{}")
    assert failed_quarantined("build_x", a, b) is True


def test_found_in_durable_dir_only(tmp_path):
    # the whole point: repo dir got git-cleaned, durable copy survives
    a = tmp_path / "repo"; b = tmp_path / "durable"
    a.mkdir(); b.mkdir()
    (b / "build_x.failed.json").write_text("{}")
    assert failed_quarantined("build_x", a, b) is True


def test_missing_dir_is_safe(tmp_path):
    a = tmp_path / "repo"; a.mkdir()
    nonexistent = tmp_path / "nope"
    assert failed_quarantined("build_x", a, nonexistent) is False
