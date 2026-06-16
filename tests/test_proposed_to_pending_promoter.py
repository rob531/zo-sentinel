"""Tests for zo_sentinel.promoters.proposed_to_pending_promoter.

These tests use pytest's tmp_path to construct isolated proposed/ and
pending/ trees. They must pass on Windows (Robin's dev host) and on the
ubuntu-latest GH runner — no /home/workspace paths, no live services.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

# Make the repo root importable regardless of pytest invocation cwd.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from zo_sentinel.promoters import proposed_to_pending_promoter as promoter  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


VALID_DESCRIPTION = (
    "This is a sufficiently long description that easily satisfies the 50-char "
    "minimum validation rule used by directive_mcp._validate."
)


def _valid_directive(task: str = "do_thing", output_file: str = "some_new_file.py") -> dict:
    return {
        "task": task,
        "handler": "generate_file",
        "output_file": output_file,
        "complexity": "medium",
        "description": VALID_DESCRIPTION,
        "source": "directive_architect",
        "proposed_at": "2026-05-27T00:00:00Z",
    }


def _write_proposal(
    proposed_dir: Path,
    name: str,
    directive: dict,
    mtime_offset_secs: float = -120.0,
) -> Path:
    """Write a proposal JSON and shift its mtime so it's old enough by default.

    mtime_offset_secs < 0 -> file appears that many seconds in the past.
    """
    proposed_dir.mkdir(parents=True, exist_ok=True)
    path = proposed_dir / name
    path.write_text(json.dumps(directive), encoding="utf-8")
    target = time.time() + mtime_offset_secs
    os.utime(path, (target, target))
    return path


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    proposed = tmp_path / "proposed"
    pending = tmp_path / "pending"
    proposed.mkdir()
    pending.mkdir()
    return proposed, pending


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_one_shot_dry_run_mixed_scenario(dirs):
    """--once --dry-run: report counts; do not move/rename anything."""
    proposed, pending = dirs

    # 3 valid old files (eligible) -- but per-cycle cap is 10 so all promote.
    for i in range(3):
        _write_proposal(proposed, f"gen_v{i}.json", _valid_directive(task=f"t{i}"))

    # 1 too-young valid file
    _write_proposal(
        proposed, "gen_young.json", _valid_directive(task="young"),
        mtime_offset_secs=-1.0,  # only 1s old, below 60s threshold
    )

    # 1 invalid (missing required field)
    invalid = _valid_directive(task="bad")
    del invalid["handler"]
    _write_proposal(proposed, "gen_bad.json", invalid)

    # 1 valid but flagged with .skip
    _write_proposal(proposed, "gen_skipped.json", _valid_directive(task="skipme"))
    (proposed / "gen_skipped.json.skip").write_text("")

    counts = promoter.run_once(
        proposed_dir=proposed,
        pending_dir=pending,
        min_age_secs=60,
        max_per_cycle=10,
        dry_run=True,
    )

    # 3 promoted (dry-run), 1 rejected, 1 skipped (skip marker), 1 too-young.
    # eligible == scanned - too_young - skipped(marker)
    assert counts["promoted"] == 3
    assert counts["rejected"] == 1
    assert counts["skipped"] == 1
    assert counts["too_young"] == 1

    # Crucially: nothing actually moved.
    assert list(pending.iterdir()) == []
    # And nothing renamed to .rejected either.
    rejected = [p for p in proposed.iterdir() if p.name.endswith(".rejected")]
    assert rejected == []


def test_one_shot_live_mixed_scenario(dirs):
    """--once (live): match the dry-run counts AND verify physical moves."""
    proposed, pending = dirs

    for i in range(3):
        _write_proposal(proposed, f"gen_v{i}.json", _valid_directive(task=f"t{i}"))

    _write_proposal(
        proposed, "gen_young.json", _valid_directive(task="young"),
        mtime_offset_secs=-1.0,
    )

    invalid = _valid_directive(task="bad")
    del invalid["handler"]
    _write_proposal(proposed, "gen_bad.json", invalid)

    _write_proposal(proposed, "gen_skipped.json", _valid_directive(task="skipme"))
    (proposed / "gen_skipped.json.skip").write_text("")

    counts = promoter.run_once(
        proposed_dir=proposed,
        pending_dir=pending,
        min_age_secs=60,
        max_per_cycle=10,
        dry_run=False,
    )

    assert counts["promoted"] == 3
    assert counts["rejected"] == 1
    assert counts["skipped"] == 1
    assert counts["too_young"] == 1

    # 3 valid files now in pending.
    promoted_names = sorted(p.name for p in pending.iterdir())
    assert promoted_names == ["gen_v0.json", "gen_v1.json", "gen_v2.json"]

    # Invalid was renamed to .rejected (still in proposed/).
    rejected = [p.name for p in proposed.iterdir() if p.name.endswith(".rejected")]
    assert rejected == ["gen_bad.json.rejected"]

    # Young file still in proposed/.
    assert (proposed / "gen_young.json").exists()
    # Skip-flagged file untouched.
    assert (proposed / "gen_skipped.json").exists()
    assert (proposed / "gen_skipped.json.skip").exists()


def test_per_cycle_cap_respected(dirs):
    """15 eligible -> only --max-per-cycle promoted, rest deferred."""
    proposed, pending = dirs

    for i in range(15):
        _write_proposal(proposed, f"gen_e{i:02d}.json", _valid_directive(task=f"task_{i}"))

    counts = promoter.run_once(
        proposed_dir=proposed,
        pending_dir=pending,
        min_age_secs=60,
        max_per_cycle=10,
        dry_run=False,
    )

    assert counts["promoted"] == 10
    assert counts["skipped"] == 5  # 5 deferred by cap
    assert len(list(pending.iterdir())) == 10
    # 5 still in proposed/.
    remaining = [p for p in proposed.iterdir() if p.suffix == ".json"]
    assert len(remaining) == 5


def test_os_replace_failure_leaves_file_in_place(dirs):
    """If os.replace raises, the source file must remain in proposed/."""
    proposed, pending = dirs
    _write_proposal(proposed, "gen_a.json", _valid_directive(task="a"))
    _write_proposal(proposed, "gen_b.json", _valid_directive(task="b"))

    real_replace = os.replace
    call_count = {"n": 0}

    def flaky_replace(src, dst):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("simulated mid-move failure")
        return real_replace(src, dst)

    with mock.patch(
        "zo_sentinel.promoters.proposed_to_pending_promoter.os.replace",
        side_effect=flaky_replace,
    ):
        counts = promoter.run_once(
            proposed_dir=proposed,
            pending_dir=pending,
            min_age_secs=60,
            max_per_cycle=10,
            dry_run=False,
        )

    # One promotion attempt failed, one succeeded.
    assert counts["promoted"] == 1
    assert counts["skipped"] == 1
    # Source for the failed promotion still in proposed/.
    files_in_proposed = sorted(p.name for p in proposed.iterdir() if p.suffix == ".json")
    files_in_pending = sorted(p.name for p in pending.iterdir())
    # Exactly one survivor on each side, no duplication, no data loss.
    assert len(files_in_proposed) == 1
    assert len(files_in_pending) == 1
    # And the two filenames are disjoint.
    assert set(files_in_proposed).isdisjoint(set(files_in_pending))


def test_invalid_json_renamed_not_moved(dirs):
    """A proposal with malformed JSON must be renamed .rejected, never moved."""
    proposed, pending = dirs
    bad = proposed / "gen_broken.json"
    bad.write_text("{this is not valid json", encoding="utf-8")
    old = time.time() - 600
    os.utime(bad, (old, old))

    counts = promoter.run_once(
        proposed_dir=proposed,
        pending_dir=pending,
        min_age_secs=60,
        max_per_cycle=10,
        dry_run=False,
    )

    assert counts["promoted"] == 0
    assert counts["rejected"] == 1
    assert list(pending.iterdir()) == []
    rejected_files = [p.name for p in proposed.iterdir() if p.name.endswith(".rejected")]
    assert rejected_files == ["gen_broken.json.rejected"]


def test_destination_collision_does_not_overwrite(dirs):
    """If pending/<name> already exists, log + skip; do not clobber."""
    proposed, pending = dirs
    name = "gen_collide.json"

    # Prior content in pending: should be preserved.
    (pending / name).write_text(json.dumps({"prior": "content"}), encoding="utf-8")
    pending_before = (pending / name).read_text(encoding="utf-8")

    _write_proposal(proposed, name, _valid_directive(task="collide"))

    counts = promoter.run_once(
        proposed_dir=proposed,
        pending_dir=pending,
        min_age_secs=60,
        max_per_cycle=10,
        dry_run=False,
    )

    assert counts["promoted"] == 0
    assert counts["skipped"] == 1
    # Original pending file still there, untouched.
    assert (pending / name).read_text(encoding="utf-8") == pending_before
    # Source still in proposed/ (NOT moved, NOT renamed).
    assert (proposed / name).exists()


def test_ttl_guard_edge_case_exactly_at_threshold(dirs):
    """A file whose age == min_age_secs is eligible (>= boundary inclusive)."""
    proposed, pending = dirs
    # File exactly 60s old; threshold is 60s -> eligible.
    p = _write_proposal(
        proposed, "gen_edge.json", _valid_directive(task="edge"),
        mtime_offset_secs=-60.0,
    )

    # Pin time.time() so the offset is precise.
    fixed_now = p.stat().st_mtime + 60.0
    with mock.patch(
        "zo_sentinel.promoters.proposed_to_pending_promoter.time.time",
        return_value=fixed_now,
    ):
        counts = promoter.run_once(
            proposed_dir=proposed,
            pending_dir=pending,
            min_age_secs=60,
            max_per_cycle=10,
            dry_run=False,
        )

    assert counts["promoted"] == 1
    assert counts["too_young"] == 0


def test_ttl_guard_just_under_threshold(dirs):
    """A file 30s old with min_age=60 must be 'too_young'."""
    proposed, pending = dirs
    _write_proposal(
        proposed, "gen_young2.json", _valid_directive(task="young2"),
        mtime_offset_secs=-30.0,
    )
    counts = promoter.run_once(
        proposed_dir=proposed,
        pending_dir=pending,
        min_age_secs=60,
        max_per_cycle=10,
        dry_run=False,
    )
    assert counts["promoted"] == 0
    assert counts["too_young"] == 1
    assert list(pending.iterdir()) == []


def test_daemon_runs_one_cycle_then_stops(dirs):
    """Mock sleep to raise StopIteration after first call; assert one cycle ran."""
    proposed, pending = dirs
    _write_proposal(proposed, "gen_d1.json", _valid_directive(task="d1"))

    call_count = {"n": 0}

    def fake_sleep(_secs):
        call_count["n"] += 1
        raise StopIteration("break out")

    promoter.run_daemon(
        proposed_dir=proposed,
        pending_dir=pending,
        poll_secs=60,
        min_age_secs=60,
        max_per_cycle=10,
        heartbeat_secs=60,
        sleep_func=fake_sleep,
    )

    # Exactly one cycle ran (one sleep call after one promotion pass).
    assert call_count["n"] == 1
    # And the cycle did its work.
    assert (pending / "gen_d1.json").exists()
    assert not (proposed / "gen_d1.json").exists()


def test_summary_line_format(dirs):
    """Cycle summary log line matches the documented format."""
    proposed, pending = dirs
    _write_proposal(proposed, "gen_s1.json", _valid_directive(task="s1"))

    counters = promoter.PromotionCounters()
    counters.scanned = 7
    counters.eligible = 5
    counters.promoted = 4
    counters.rejected = 1
    counters.skipped = 2
    counters.too_young = 0

    line = counters.summary_line()
    assert line == (
        "cycle: scanned=7 eligible=5 promoted=4 rejected=1 skipped=2"
    )


def test_skip_marker_holds_file_out(dirs):
    """A sibling .skip file prevents promotion even if everything else is fine."""
    proposed, pending = dirs
    _write_proposal(proposed, "gen_hold.json", _valid_directive(task="hold"))
    (proposed / "gen_hold.json.skip").write_text("")

    counts = promoter.run_once(
        proposed_dir=proposed,
        pending_dir=pending,
        min_age_secs=60,
        max_per_cycle=10,
        dry_run=False,
    )

    assert counts["promoted"] == 0
    assert counts["skipped"] == 1
    assert (proposed / "gen_hold.json").exists()
    assert list(pending.iterdir()) == []


def test_done_and_failed_files_ignored(dirs):
    """Files ending in .done.json / .failed.json are not candidates."""
    proposed, pending = dirs
    (proposed / "gen_done.done.json").write_text(json.dumps(_valid_directive()))
    (proposed / "gen_fail.failed.json").write_text(json.dumps(_valid_directive()))

    # Shift mtimes well into the past so the TTL wouldn't filter them.
    for name in ("gen_done.done.json", "gen_fail.failed.json"):
        p = proposed / name
        old = time.time() - 600
        os.utime(p, (old, old))

    counts = promoter.run_once(
        proposed_dir=proposed,
        pending_dir=pending,
        min_age_secs=60,
        max_per_cycle=10,
        dry_run=False,
    )

    assert counts["scanned"] == 0
    assert counts["promoted"] == 0
    # Both still in proposed/.
    assert (proposed / "gen_done.done.json").exists()
    assert (proposed / "gen_fail.failed.json").exists()
    assert list(pending.iterdir()) == []


def test_cli_once_dry_run_via_main(dirs, capsys):
    """End-to-end: invoke main() with argv to confirm wiring."""
    proposed, pending = dirs
    _write_proposal(proposed, "gen_cli.json", _valid_directive(task="cli"))

    rc = promoter.main([
        "--once",
        "--dry-run",
        "--proposed-dir", str(proposed),
        "--pending-dir", str(pending),
        "--min-age-secs", "60",
        "--max-per-cycle", "10",
    ])
    assert rc == 0
    # Dry-run -> nothing moved.
    assert list(pending.iterdir()) == []
    assert (proposed / "gen_cli.json").exists()


def test_validator_too_short_description(dirs):
    """Description < 50 chars must be rejected."""
    proposed, pending = dirs
    d = _valid_directive()
    d["description"] = "too short"
    _write_proposal(proposed, "gen_short.json", d)

    counts = promoter.run_once(
        proposed_dir=proposed,
        pending_dir=pending,
        min_age_secs=60,
        max_per_cycle=10,
        dry_run=False,
    )
    assert counts["rejected"] == 1
    assert (proposed / "gen_short.json.rejected").exists()


# ---------------------------------------------------------------------------
# Done-sentinel idempotency (mirrors goose_runner.mark_directive_completed)
# ---------------------------------------------------------------------------


def test_done_sentinel_present_promotion_is_skipped(tmp_path):
    """If <directives_root>/<directive_id>.done.json exists, skip promotion.

    Renames the proposal to .duplicate so it does not get re-scanned.
    Mirrors goose_runner's own already-built short-circuit.
    """
    directives_root = tmp_path / "directives"
    proposed = directives_root / "proposed"
    pending = directives_root / "pending"
    proposed.mkdir(parents=True)
    pending.mkdir()

    # Already-built sentinel — same path shape as goose_runner writes.
    sentinel = directives_root / "breaker_action_investigate_write_service.done.json"
    sentinel.write_text("{}", encoding="utf-8")

    directive = _valid_directive(task="breaker_action_investigate_write_service")
    directive["directive_id"] = "breaker_action_investigate_write_service"
    _write_proposal(proposed, "gen_aaaa1111.json", directive)

    counts = promoter.run_once(
        proposed_dir=proposed,
        pending_dir=pending,
        min_age_secs=60,
        max_per_cycle=10,
        dry_run=False,
        directives_root=directives_root,
    )

    assert counts["promoted"] == 0
    assert counts["skipped"] == 1
    assert counts["rejected"] == 0
    # Source got renamed, not moved into pending.
    assert not (proposed / "gen_aaaa1111.json").exists()
    assert (proposed / "gen_aaaa1111.json.duplicate").exists()
    assert list(pending.iterdir()) == []


def test_done_sentinel_absent_promotion_proceeds(tmp_path):
    """No sentinel -> normal promotion path."""
    directives_root = tmp_path / "directives"
    proposed = directives_root / "proposed"
    pending = directives_root / "pending"
    proposed.mkdir(parents=True)
    pending.mkdir()

    directive = _valid_directive(task="brand_new_thing")
    directive["directive_id"] = "brand_new_thing"
    _write_proposal(proposed, "gen_bbbb2222.json", directive)

    counts = promoter.run_once(
        proposed_dir=proposed,
        pending_dir=pending,
        min_age_secs=60,
        max_per_cycle=10,
        dry_run=False,
        directives_root=directives_root,
    )

    assert counts["promoted"] == 1
    assert counts["skipped"] == 0
    assert (pending / "gen_bbbb2222.json").exists()
    assert not (proposed / "gen_bbbb2222.json").exists()


def test_done_sentinel_dry_run_no_filesystem_change(tmp_path):
    """--dry-run must not create the .duplicate rename even on sentinel hit."""
    directives_root = tmp_path / "directives"
    proposed = directives_root / "proposed"
    pending = directives_root / "pending"
    proposed.mkdir(parents=True)
    pending.mkdir()

    sentinel = directives_root / "already_done.done.json"
    sentinel.write_text("{}", encoding="utf-8")

    directive = _valid_directive(task="already_done")
    directive["directive_id"] = "already_done"
    src = _write_proposal(proposed, "gen_cccc3333.json", directive)

    counts = promoter.run_once(
        proposed_dir=proposed,
        pending_dir=pending,
        min_age_secs=60,
        max_per_cycle=10,
        dry_run=True,
        directives_root=directives_root,
    )

    assert counts["promoted"] == 0
    assert counts["skipped"] == 1
    assert src.exists()  # untouched
    assert not (proposed / "gen_cccc3333.json.duplicate").exists()
    assert list(pending.iterdir()) == []


def test_directives_root_defaults_to_pending_parent(tmp_path):
    """When directives_root is None, it defaults to pending_dir.parent.

    Layout: tmp/directives/{proposed,pending,<id>.done.json}
    """
    directives_root = tmp_path / "directives"
    proposed = directives_root / "proposed"
    pending = directives_root / "pending"
    proposed.mkdir(parents=True)
    pending.mkdir()

    sentinel = directives_root / "auto_root.done.json"
    sentinel.write_text("{}", encoding="utf-8")

    directive = _valid_directive(task="auto_root")
    directive["directive_id"] = "auto_root"
    _write_proposal(proposed, "gen_dddd4444.json", directive)

    counts = promoter.run_once(
        proposed_dir=proposed,
        pending_dir=pending,
        min_age_secs=60,
        max_per_cycle=10,
        dry_run=False,
        # directives_root deliberately omitted -> defaults to pending.parent
    )

    assert counts["promoted"] == 0
    assert counts["skipped"] == 1
    assert (proposed / "gen_dddd4444.json.duplicate").exists()


def test_missing_directive_id_falls_through_to_normal_promotion(tmp_path):
    """If the directive JSON lacks both directive_id and id, the done-sentinel
    check has no key to look up. Don't crash; treat as normal promotion."""
    directives_root = tmp_path / "directives"
    proposed = directives_root / "proposed"
    pending = directives_root / "pending"
    proposed.mkdir(parents=True)
    pending.mkdir()

    directive = _valid_directive(task="anonymous_directive")
    # explicitly no directive_id / id keys
    directive.pop("directive_id", None)
    directive.pop("id", None)
    _write_proposal(proposed, "gen_eeee5555.json", directive)

    counts = promoter.run_once(
        proposed_dir=proposed,
        pending_dir=pending,
        min_age_secs=60,
        max_per_cycle=10,
        dry_run=False,
        directives_root=directives_root,
    )

    assert counts["promoted"] == 1
    assert (pending / "gen_eeee5555.json").exists()


def test_duplicate_rename_is_bounded_no_suffix_bump(tmp_path):
    """A re-archived duplicate CLOBBERS the single .duplicate (bounded, no suffix
    bump). The architect re-proposes terminal directives every cycle, so
    suffix-bumping would flood proposed/ with .duplicate.1/.2/... -- PR #187 made
    _rename_duplicate clobber to exactly one .duplicate per basename."""
    directives_root = tmp_path / "directives"
    proposed = directives_root / "proposed"
    pending = directives_root / "pending"
    proposed.mkdir(parents=True)
    pending.mkdir()

    sentinel = directives_root / "collision_id.done.json"
    sentinel.write_text("{}", encoding="utf-8")

    # Pre-existing .duplicate from a previous pass
    (proposed / "gen_ffff6666.json.duplicate").write_text("{}", encoding="utf-8")

    directive = _valid_directive(task="collision_id")
    directive["directive_id"] = "collision_id"
    _write_proposal(proposed, "gen_ffff6666.json", directive)

    counts = promoter.run_once(
        proposed_dir=proposed,
        pending_dir=pending,
        min_age_secs=60,
        max_per_cycle=10,
        dry_run=False,
        directives_root=directives_root,
    )

    assert counts["skipped"] == 1
    assert (proposed / "gen_ffff6666.json.duplicate").exists()
    # bounded: clobber to a single .duplicate, never bump a .1 suffix (PR #187)
    assert not (proposed / "gen_ffff6666.json.duplicate.1").exists()
    assert not (proposed / "gen_ffff6666.json").exists()
