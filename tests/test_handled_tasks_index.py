"""The dedup memory must survive the corpus being archived.

tools/compact_proposed_corpus.py folds directives/proposed/ into one
handled_tasks.json so 7,977 inodes can go away. If _terminal_task_stems()
did not read that index, clearing the directory would re-open the 798x
re-proposal loop the index exists to close.
"""
import importlib
import json

import pytest


@pytest.fixture()
def gen(tmp_path, monkeypatch):
    mod = importlib.import_module("zo_sentinel.sentinel_directive_generator_goose")
    proposed = tmp_path / "directives" / "proposed"
    pending = tmp_path / "directives" / "pending"
    proposed.mkdir(parents=True)
    pending.mkdir(parents=True)
    monkeypatch.setattr(mod, "PROPOSED_DIR", proposed, raising=False)
    monkeypatch.setattr(mod, "PENDING_DIR", pending, raising=False)
    return mod, proposed


def _write_index(proposed, tasks):
    (proposed.parent / "handled_tasks.json").write_text(
        json.dumps({"schema": 1, "tasks": tasks}), encoding="utf-8")


def test_index_supplies_stems_with_empty_dir(gen):
    """The whole point: no marker files, memory intact."""
    mod, proposed = gen
    _write_index(proposed, {"build_service_risk_tier_trend":
                            {"n": 798, "states": {"expanded": 798}}})
    assert not any(proposed.iterdir())
    assert "build_service_risk_tier_trend" in mod._queued_stems()


def test_index_respects_the_settled_boundary(gen):
    """.rejected in the index must NOT suppress -- same FU-011 rule."""
    mod, proposed = gen
    _write_index(proposed, {"build_service_declined":
                            {"n": 4, "states": {"rejected": 4}}})
    assert "build_service_declined" not in mod._queued_stems()


def test_index_and_markers_union(gen):
    mod, proposed = gen
    _write_index(proposed, {"from_index": {"n": 2, "states": {"expanded": 2}}})
    (proposed / "gen_1473c4a0_from_marker.json.expanded").write_text("{}", encoding="utf-8")
    stems = mod._queued_stems()
    assert "from_index" in stems and "from_marker" in stems


def test_corrupt_index_does_not_break_dedup(gen):
    """The index is an optimisation. A bad one must not take down the loop."""
    mod, proposed = gen
    (proposed.parent / "handled_tasks.json").write_text("{not json", encoding="utf-8")
    (proposed / "gen_1473c4a0_still_works.json.expanded").write_text("{}", encoding="utf-8")
    assert "still_works" in mod._queued_stems()
