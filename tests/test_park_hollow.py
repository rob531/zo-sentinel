"""Parking a refused build: done != merged.

A hollow build stamps <task>.done.json when it completes and the PR opens. When
the publisher then REFUSES that PR, the sentinel is left asserting a success that
will never land -- is_goose_eligible skips the directive forever, and any reseed
under the same name is silently swallowed (the *_v2 tax). These tests pin the two
properties that make parking safe: it is DURABLE (survives a `git clean` of the
repo tree) and it does NOT re-admit the directive to the builder.
"""
import json

import pytest

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


def test_publisher_parks_the_directive_when_it_refuses_a_hollow_build(tmp_path):
    from tests.test_pr_publisher import InMemoryMeshStore, _artifact
    from zo_sentinel.publisher.publisher import Publisher

    home, durable = tmp_path / "home", tmp_path / "quarantine"
    (home / "directives").mkdir(parents=True)
    (home / "directives" / "build_x.done.json").write_text("{}", encoding="utf-8")

    pub = Publisher(InMemoryMeshStore(artifacts=[_artifact("hollow_api.py")]),
                    home=str(home), quarantine_dir=str(durable))
    pub._resolver = lambda art: "from fastapi import FastAPI\napp = FastAPI()\n"
    res = pub.run_once()

    assert res[0]["action"] == "hollow_blocked"
    assert res[0]["parked"] is True
    assert failed_quarantined("build_x", home / "directives", durable)
    assert not (home / "directives" / "build_x.done.json").exists()


def test_publisher_does_not_park_a_hollow_build_it_cannot_attribute(tmp_path):
    """No task on the artifact -> we do not know WHICH directive to park, and
    parking the wrong one would silence a healthy directive. Refuse the PR, park
    nothing."""
    from tests.test_pr_publisher import InMemoryMeshStore, _artifact
    from zo_sentinel.publisher.publisher import Publisher

    home, durable = tmp_path / "home", tmp_path / "quarantine"
    (home / "directives").mkdir(parents=True)

    pub = Publisher(InMemoryMeshStore(artifacts=[_artifact("hollow_api.py", task="")]),
                    home=str(home), quarantine_dir=str(durable))
    pub._resolver = lambda art: "from fastapi import FastAPI\napp = FastAPI()\n"
    res = pub.run_once()

    assert res[0]["action"] == "hollow_blocked"
    assert res[0]["parked"] is False
    assert not list(durable.iterdir()) if durable.exists() else True


def test_parked_directive_is_not_re_admitted_to_the_builder(tmp_path):
    """The whole point of park-vs-delete: a hollow build re-admitted just rebuilds
    hollow ('clearing first just re-ghosts them', 2026-06-13). .failed is never
    self-healed, so the directive stays out of the loop until someone acts.
    """
    d = tmp_path / "directives"
    d.mkdir()
    park_directive("build_x", "hollow", WHEN, d)
    assert failed_quarantined("build_x", d), "a parked directive must stay parked"