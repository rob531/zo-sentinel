"""The architect's dedup set must survive the promoter's rename.

Regression for the 798x re-proposal loop: the promoter renames a handled
parent to `<name>.json.expanded`, which stops matching the `*.json` glob in
_queued_stems(). The task therefore LEFT the dedup set the moment it was
successfully handled, and the architect re-proposed it every cycle forever.
Measured on the live corpus: 7,976 proposals over 2,103 distinct tasks,
`build_service_risk_tier_trend` proposed 798 times, 77.2% of the corpus
avoidable.

These tests also pin the FU-011 boundary: .rejected and .revived must NOT
suppress, because the system declined that attempt and a later, better one is
legitimate.
"""
import importlib

import pytest


@pytest.fixture()
def gen(tmp_path, monkeypatch):
    mod = importlib.import_module(
        "zo_sentinel.sentinel_directive_generator_goose")
    proposed = tmp_path / "directives" / "proposed"
    pending = tmp_path / "directives" / "pending"
    proposed.mkdir(parents=True)
    pending.mkdir(parents=True)
    monkeypatch.setattr(mod, "PROPOSED_DIR", proposed, raising=False)
    monkeypatch.setattr(mod, "PENDING_DIR", pending, raising=False)
    return mod, proposed


def test_expanded_parent_still_suppresses(gen):
    """The core bug: a handled task must stay in the dedup set."""
    mod, proposed = gen
    (proposed / "salvage_20260914171001_build_service_risk_tier_trend.json.expanded"
     ).write_text("{}", encoding="utf-8")

    assert "build_service_risk_tier_trend" in mod._terminal_task_stems()
    assert "build_service_risk_tier_trend" in mod._queued_stems(), (
        "a task whose proposal was expanded must remain deduped -- otherwise "
        "the architect re-proposes it every cycle (798x observed in the wild)"
    )


def test_gen_prefix_is_stripped(gen):
    """Producer prefixes are on the filename, not the task name."""
    mod, proposed = gen
    (proposed / "gen_1473c4a0_build_service_verdict_breakdown.json.expanded"
     ).write_text("{}", encoding="utf-8")
    assert "build_service_verdict_breakdown" in mod._queued_stems()


def test_duplicate_marker_suppresses(gen):
    mod, proposed = gen
    (proposed / "svc_services_staged_foo_service_toml.json.duplicate"
     ).write_text("{}", encoding="utf-8")
    assert "svc_services_staged_foo_service_toml" in mod._queued_stems()


def test_collision_counter_tolerated(gen):
    """The promoter appends .1/.2 on collision; that must not defeat the strip."""
    mod, proposed = gen
    (proposed / "gen_095ad57c_build_service_thing.json.expanded.2"
     ).write_text("{}", encoding="utf-8")
    assert "build_service_thing" in mod._queued_stems()


@pytest.mark.parametrize("suffix", [".rejected", ".revived"])
def test_declined_attempts_do_not_suppress(gen, suffix):
    """FU-011 boundary -- do not regress into over-suppression.

    The system declined (or deliberately revived) that attempt. A later,
    better proposal for the same task is legitimate and must get through.
    """
    mod, proposed = gen
    (proposed / ("salvage_20260914122931_build_service_declined.json" + suffix)
     ).write_text("{}", encoding="utf-8")
    assert "build_service_declined" not in mod._queued_stems(), (
        "%s must not suppress a live re-proposal" % suffix
    )


def test_bak_files_still_ignored(gen):
    """FU-011 proper: a stale .bak must never suppress."""
    mod, proposed = gen
    (proposed / "build_service_stale.json.bak.20260628_174036.expanded"
     ).write_text("{}", encoding="utf-8")
    assert not any("stale" in s for s in mod._terminal_task_stems())


def test_empty_dir_is_safe(gen):
    mod, _ = gen
    assert mod._terminal_task_stems() == set()
