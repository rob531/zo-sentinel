"""Tests for zo_sentinel.queue_janitor + the promoter's durable-quarantine
awareness and janitor wiring.

Mirrors REAL queue variance, not uniform toy inputs (ops-discipline lesson):
mixed create/edit-class directives, task-keyed generator files (no
directive_id), unparseable JSON, open-lesson rebuild requests, durable vs
in-repo quarantine sentinels.
"""
from __future__ import annotations

import json
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from zo_sentinel import queue_janitor  # noqa: E402
from zo_sentinel.build_lessons import record_lesson  # noqa: E402
from zo_sentinel.promoters import proposed_to_pending_promoter as promoter  # noqa: E402


# ---------------------------------------------------------------------------
# Layout helpers -- canonical home/{directives/{proposed,pending},lessons}
# ---------------------------------------------------------------------------

def _mk_home(tmp_path):
    home = tmp_path / "home"
    for sub in ("directives/proposed", "directives/pending", "lessons"):
        (home / sub).mkdir(parents=True)
    quarantine = tmp_path / "state" / "quarantine"
    quarantine.mkdir(parents=True)
    return home, home / "directives", quarantine


def _directive(task, output_file=None, **extra):
    d = {
        "task": task,
        "handler": "generate_file",
        "output_file": output_file if output_file is not None else f"{task}.py",
        "description": "x" * 60,  # passes the promoter's >=50 char validator
    }
    d.update(extra)
    return d


def _write(queue_dir, name, directive):
    p = queue_dir / name
    p.write_text(json.dumps(directive), encoding="utf-8")
    return p


def _build_output(home, filename, size=64):
    out = home / filename
    out.write_text("#" * size, encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# queue_janitor.run_pass
# ---------------------------------------------------------------------------

def test_retires_redundant_pending(tmp_path):
    home, directives, quarantine = _mk_home(tmp_path)
    _build_output(home, "built_api.py")
    _write(directives / "pending", "gen_1_build_built_api.json",
           _directive("build_built_api", "built_api.py"))

    stats = queue_janitor.run_pass(directives, quarantine_dirs=[directives, quarantine])

    assert stats["retired"] == 1
    assert stats["by_class"] == {"pending_redundant": 1}
    assert not list((directives / "pending").glob("*.json"))
    moved = list((directives / "retired").rglob("gen_1_build_built_api.json"))
    assert len(moved) == 1 and "pending_redundant" in str(moved[0])


def test_retires_quarantined_pending_via_durable_store(tmp_path):
    home, directives, quarantine = _mk_home(tmp_path)
    # Task-keyed generator directive: identity resolves via `task`.
    _write(directives / "pending", "gen_2_build_hard_thing.json",
           _directive("build_hard_thing"))
    (quarantine / "build_hard_thing.failed.json").write_text("{}", encoding="utf-8")

    stats = queue_janitor.run_pass(directives, quarantine_dirs=[directives, quarantine])

    assert stats["by_class"] == {"pending_quarantined": 1}
    assert not list((directives / "pending").glob("*.json"))


def test_keeps_open_lesson_rebuild_request(tmp_path):
    """Output exists BUT an open lesson says rebuild -- must be kept (it is a
    genuine rebuild request, exactly what goose's dedup skip also honors)."""
    home, directives, quarantine = _mk_home(tmp_path)
    _build_output(home, "flaky_mod.py")
    record_lesson(home / "lessons", "flaky_mod.py", "build_flaky_mod",
                  "ghost_build", "self-test failed")
    f = _write(directives / "pending", "gen_3_build_flaky_mod.json",
               _directive("build_flaky_mod", "flaky_mod.py"))

    stats = queue_janitor.run_pass(directives, quarantine_dirs=[directives, quarantine])

    assert stats["retired"] == 0
    assert f.exists()


def test_keeps_edit_class_even_when_target_exists(tmp_path):
    """wire_* directives modify existing files (declared_output -> None); the
    bogus stamped output_file existing must NOT retire them."""
    home, directives, quarantine = _mk_home(tmp_path)
    _build_output(home, "wire_admin_page.py")
    f = _write(directives / "pending", "gen_4_wire_admin_page.json",
               _directive("wire_admin_page", "wire_admin_page.py"))

    stats = queue_janitor.run_pass(directives, quarantine_dirs=[directives, quarantine])

    assert stats["retired"] == 0
    assert f.exists()


def test_retires_redundant_and_quarantined_proposed(tmp_path):
    """Re-proposals of built/quarantined work are retired AT THE SOURCE, which
    directly lowers proposed/ depth below the architect's cap."""
    home, directives, quarantine = _mk_home(tmp_path)
    _build_output(home, "already_built.py")
    _write(directives / "proposed", "gen_5_build_already_built.json",
           _directive("build_already_built", "already_built.py"))
    _write(directives / "proposed", "gen_6_build_gave_up.json",
           _directive("build_gave_up"))
    (quarantine / "build_gave_up.failed.json").write_text("{}", encoding="utf-8")
    novel = _write(directives / "proposed", "gen_7_build_novel_thing.json",
                   _directive("build_novel_thing"))

    stats = queue_janitor.run_pass(directives, quarantine_dirs=[directives, quarantine])

    assert stats["by_class"] == {"proposed_redundant": 1, "proposed_quarantined": 1}
    assert novel.exists()  # novel work untouched


def test_unparseable_and_nonjson_files_untouched(tmp_path):
    home, directives, quarantine = _mk_home(tmp_path)
    junk = (directives / "pending") / "gen_8_junk.json"
    junk.write_text("{not json", encoding="utf-8")
    sentinel = (directives / "pending") / "old.failed.json"
    sentinel.write_text("{}", encoding="utf-8")

    stats = queue_janitor.run_pass(directives, quarantine_dirs=[directives, quarantine])

    assert junk.exists() and sentinel.exists()
    assert stats["retired"] == 0 and stats["errors"] == 1


def test_limit_bounds_a_pass(tmp_path):
    home, directives, quarantine = _mk_home(tmp_path)
    for i in range(5):
        _build_output(home, f"mod_{i}.py")
        _write(directives / "pending", f"gen_9{i}_build_mod_{i}.json",
               _directive(f"build_mod_{i}", f"mod_{i}.py"))

    stats = queue_janitor.run_pass(directives, limit=2,
                                   quarantine_dirs=[directives, quarantine])

    assert stats["retired"] == 2
    assert len(list((directives / "pending").glob("*.json"))) == 3


def test_missing_dirs_are_noop(tmp_path):
    stats = queue_janitor.run_pass(tmp_path / "nope" / "directives")
    assert stats["retired"] == 0


# ---------------------------------------------------------------------------
# enabled() gating
# ---------------------------------------------------------------------------

def test_posture_comes_from_declarative_policy(tmp_path, monkeypatch):
    """Since the policy layer, the gate's default is DECLARED in
    zo_sentinel/policy_defaults.toml (janitor = true), not hardcoded off."""
    monkeypatch.delenv(queue_janitor.ENV_FLAG, raising=False)
    monkeypatch.setenv("ZO_POLICY_OVERRIDE_PATH", str(tmp_path / "ov.json"))
    assert queue_janitor.enabled(tmp_path) is True     # declared posture
    monkeypatch.setenv(queue_janitor.ENV_FLAG, "0")    # env big-hammer off
    assert queue_janitor.enabled(tmp_path) is False


def test_enabled_via_sentinel_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv(queue_janitor.ENV_FLAG, raising=False)
    monkeypatch.setenv("ZO_POLICY_OVERRIDE_PATH", str(tmp_path / "ov.json"))
    sf = tmp_path / queue_janitor.SENTINEL_NAME
    sf.write_text("1", encoding="utf-8")
    assert queue_janitor.enabled(tmp_path) is True
    sf.write_text("0", encoding="utf-8")   # live off-switch (legacy, honored)
    assert queue_janitor.enabled(tmp_path) is False
    monkeypatch.setenv(queue_janitor.ENV_FLAG, "1")
    assert queue_janitor.enabled(tmp_path) is True


# ---------------------------------------------------------------------------
# Promoter integration: durable-quarantine awareness + gated janitor
# ---------------------------------------------------------------------------

def test_promoter_archives_reproposal_of_durably_quarantined_id(tmp_path, monkeypatch):
    home, directives, quarantine = _mk_home(tmp_path)
    monkeypatch.setenv("ZO_DURABLE_QUARANTINE_DIR", str(quarantine))
    # Janitor OFF here: this test targets the promoter's OWN durable-terminal
    # archival path (the janitor would otherwise retire the file first).
    monkeypatch.setenv(queue_janitor.ENV_FLAG, "0")
    (quarantine / "build_gave_up.failed.json").write_text("{}", encoding="utf-8")
    src = _write(directives / "proposed", "gen_10_build_gave_up.json",
                 _directive("build_gave_up"))

    promoter.run_once(directives / "proposed", directives / "pending",
                      min_age_secs=0, max_per_cycle=10,
                      directives_root=directives)

    # Archived as .duplicate, NOT promoted into pending (would re-squat).
    assert not src.exists()
    assert (directives / "proposed" / (src.name + ".duplicate")).exists()
    assert not list((directives / "pending").glob("*.json"))


def test_promoter_treats_durably_quarantined_squatter_as_terminal(tmp_path, monkeypatch):
    home, directives, quarantine = _mk_home(tmp_path)
    monkeypatch.setenv("ZO_DURABLE_QUARANTINE_DIR", str(quarantine))
    # Janitor OFF: exercises the promoter's own terminal-squatter branch.
    monkeypatch.setenv(queue_janitor.ENV_FLAG, "0")
    # Squatter in pending, quarantined ONLY in the durable store.
    _write(directives / "pending", "gen_11_build_squat.json", _directive("build_squat"))
    (quarantine / "build_squat.failed.json").write_text("{}", encoding="utf-8")
    # Fresh same-filename proposal: previously "collision (non-terminal); skip"
    # FOREVER -- now recognized as a terminal squatter and archived.
    src = _write(directives / "proposed", "gen_11_build_squat.json",
                 _directive("build_squat"))

    out = promoter.run_once(directives / "proposed", directives / "pending",
                            min_age_secs=0, max_per_cycle=10,
                            directives_root=directives)

    assert not src.exists()
    assert (directives / "proposed" / (src.name + ".duplicate")).exists()
    assert out["promoted"] == 0


def test_promoter_janitor_off_then_unclogs_when_enabled(tmp_path, monkeypatch):
    """End-to-end unclog: redundant squatter in pending + novel proposal.
    Gate off (env big-hammer) -> squatter stays. Gate on -> squatter retired,
    novel promoted."""
    home, directives, quarantine = _mk_home(tmp_path)
    monkeypatch.setenv("ZO_DURABLE_QUARANTINE_DIR", str(quarantine))
    monkeypatch.setenv("ZO_POLICY_OVERRIDE_PATH", str(tmp_path / "ov.json"))
    monkeypatch.setenv(queue_janitor.ENV_FLAG, "0")
    _build_output(home, "old_mod.py")
    squatter = _write(directives / "pending", "gen_12_build_old_mod.json",
                      _directive("build_old_mod", "old_mod.py"))
    novel = _write(directives / "proposed", "gen_13_build_new_mod.json",
                   _directive("build_new_mod"))

    out = promoter.run_once(directives / "proposed", directives / "pending",
                            min_age_secs=0, max_per_cycle=10,
                            directives_root=directives)
    assert squatter.exists()  # gate OFF: zero janitor behavior
    assert out["promoted"] == 1                              # novel promoted...
    assert (directives / "pending" / novel.name).exists()    # ...into pending

    monkeypatch.delenv(queue_janitor.ENV_FLAG, raising=False)  # declared posture = ON
    promoter.run_once(directives / "proposed", directives / "pending",
                      min_age_secs=0, max_per_cycle=10,
                      directives_root=directives)

    assert not squatter.exists()          # squatter retired by the janitor
    assert (directives / "pending" / novel.name).exists()  # novel work untouched


def test_promoter_dry_run_never_mutates(tmp_path, monkeypatch):
    home, directives, quarantine = _mk_home(tmp_path)
    monkeypatch.setenv("ZO_DURABLE_QUARANTINE_DIR", str(quarantine))
    (directives / queue_janitor.SENTINEL_NAME).write_text("1", encoding="utf-8")
    _build_output(home, "dr_mod.py")
    squatter = _write(directives / "pending", "gen_14_build_dr_mod.json",
                      _directive("build_dr_mod", "dr_mod.py"))

    promoter.run_once(directives / "proposed", directives / "pending",
                      min_age_secs=0, max_per_cycle=10, dry_run=True,
                      directives_root=directives)

    assert squatter.exists()


def test_iter_directive_files_excludes_bak_and_duplicate_backups(tmp_path):
    """FU-020(b): backup/dup copies that still end in .json (".bak_<id>.json",
    ".duplicate_<id>.json") must NOT be counted as live directives -- they are
    the stale files that mask queue emptiness (FU-011). Real directives plus the
    done/failed terminal sentinels behave unchanged."""
    home, directives, _ = _mk_home(tmp_path)
    pending = directives / "pending"
    live = _write(pending, "realdir.json", _directive("realdir"))
    _write(pending, ".bak_realdir.json", _directive("realdir"))
    _write(pending, ".duplicate_realdir.json", _directive("realdir"))
    _write(pending, "realdir.done.json", _directive("realdir"))
    _write(pending, "realdir.failed.json", _directive("realdir"))
    assert queue_janitor._iter_directive_files(pending) == [live]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
