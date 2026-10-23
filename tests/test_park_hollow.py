"""Parking a refused build: done != merged.

A hollow build stamps <task>.done.json when it completes and the PR opens. When
the publisher then REFUSES that PR, the sentinel is left asserting a success that
will never land -- is_goose_eligible skips the directive forever, and any reseed
under the same name is silently swallowed (the *_v2 tax). These tests pin the two
properties that make parking safe: it is DURABLE (survives a `git clean` of the
repo tree) and it does NOT re-admit the directive to the builder.
"""
import json


from zo_sentinel.build_completion import failed_quarantined, park_directive

WHEN = "2026-07-13T12:00:00Z"


def test_park_writes_failed_and_clears_the_false_done(tmp_path):
    d = tmp_path / "directives"
    d.mkdir()
    (d / "build_x.done.json").write_text('{"directive_id": "build_x"}', encoding="utf-8")

    assert park_directive("build_x", "hollow", WHEN, d) is True

    assert not (d / "build_x.done.json").exists(), "the false 'done' must not survive"
    parked = json.loads((d / "build_x.failed.json").read_text(encoding="utf-8"))
    assert parked["directive_id"] == "build_x"
    assert parked["reason"] == "hollow"


def test_park_is_durable_across_a_git_clean_of_the_repo_tree(tmp_path):
    """`git clean` on daemon respawn wipes untracked sentinels under directives/.
    The durable copy lives outside the tree, so the park survives -- otherwise the
    directive silently un-parks and re-enters the builder (the re-flush treadmill).
    """
    d, durable = tmp_path / "directives", tmp_path / "state" / "quarantine"
    d.mkdir()
    park_directive("build_x", "hollow", WHEN, d, durable)
    assert failed_quarantined("build_x", d, durable)

    for f in d.iterdir():          # simulate `git clean -fd` on the repo tree
        f.unlink()

    assert not (d / "build_x.failed.json").exists()
    assert failed_quarantined("build_x", d, durable), "park did not survive git clean"


def test_park_never_raises_on_an_unwritable_dir(tmp_path):
    # bookkeeping must never break the caller that is mid-publish
    assert park_directive("build_x", "why", WHEN, tmp_path / "nope" / "deep") in (True, False)


# The two publisher-refusal cases (park with a task, park nothing without one)
# moved with the refusal itself to the producer: tests/test_producer_commit.py
# test_hollow_build_parks_its_directive / test_hollow_build_without_a_task_parks_nothing.


def test_parked_directive_is_not_re_admitted_to_the_builder(tmp_path):
    """The whole point of park-vs-delete: a hollow build re-admitted just rebuilds
    hollow ('clearing first just re-ghosts them', 2026-06-13). .failed is never
    self-healed, so the directive stays out of the loop until someone acts.
    """
    d = tmp_path / "directives"
    d.mkdir()
    park_directive("build_x", "hollow", WHEN, d)
    assert failed_quarantined("build_x", d), "a parked directive must stay parked"