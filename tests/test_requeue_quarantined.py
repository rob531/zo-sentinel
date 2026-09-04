"""Tests for tools/requeue_quarantined.py (issue #4079).

The property under test is NOT "the tool runs". It is that the two things
standing between a manifest of 69 candidates and 69 simultaneous PRs -- the
per-run cap and the live-state idempotence check -- are load-bearing.

Every guard here carries its own NEGATIVE CONTROL: the same fixture is run a
second time with the guard defeated, and the test asserts the answer CHANGES.
A guard that is never observed permitting what it is supposed to forbid is an
assertion nobody has seen go red, and this repo has 75 entries' worth of those.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import requeue_quarantined as rq  # noqa: E402


# --------------------------------------------------------------- fixtures ---

def _manifest(candidates):
    return {
        "quarantined_at": "2026-08-26",
        "files": [{"from": c, "to": "quarantine/%s" % c,
                   "first_added_to_main": "2026-05-25",
                   "phantom_tables": ["mcp_servers"]} for c in candidates],
        "re_emission": {"path": "grounded engine path",
                        "status": "QUEUED", "candidates": list(candidates)},
    }


@pytest.fixture
def world(tmp_path):
    """A miniature repo: 3 candidates, only 2 of them referenced by live code."""
    repo = tmp_path / "repo"
    (repo / "quarantine").mkdir(parents=True)
    (repo / "tools").mkdir()
    for c in ("alpha.py", "beta.py", "gamma.py"):
        (repo / "quarantine" / c).write_text("# withdrawn\n", encoding="utf-8")
    # live code that still calls alpha and beta; nothing calls gamma
    (repo / "caller.py").write_text("import alpha\nfrom beta import thing\n",
                                    encoding="utf-8")
    # a file that only names ITSELF must not count as a referrer
    (repo / "quarantine" / "gamma.py").write_text("gamma = 1\n", encoding="utf-8")
    ddir = tmp_path / "directives"
    ddir.mkdir()
    return {"repo": repo, "ddir": ddir,
            "manifest": _manifest(["alpha.py", "beta.py", "gamma.py"])}


# ---------------------------------------------------------- policy: dead ----

def test_unreferenced_candidate_is_not_selected(world):
    p = rq.plan(world["repo"], world["manifest"], world["ddir"], limit=5)
    picked = [r["candidate"] for r in p["eligible"]]
    assert "gamma.py" not in picked
    assert set(picked) == {"alpha.py", "beta.py"}


def test_negative_control_policy_all_admits_the_dead_one(world):
    """NEGATIVE CONTROL for the referenced-only policy: defeat it and gamma,
    which the default refused, must appear. If both policies give the same
    answer the filter is decorative."""
    strict = rq.plan(world["repo"], world["manifest"], world["ddir"],
                     limit=5, policy="referenced")
    loose = rq.plan(world["repo"], world["manifest"], world["ddir"],
                    limit=5, policy="all")
    assert "gamma.py" not in [r["candidate"] for r in strict["eligible"]]
    assert "gamma.py" in [r["candidate"] for r in loose["eligible"]]


def test_self_reference_does_not_count_as_live(tmp_path):
    repo = tmp_path / "repo"
    (repo / "quarantine").mkdir(parents=True)
    (repo / "solo.py").unlink(missing_ok=True) if (repo / "solo.py").exists() else None
    # the ONLY text naming 'solo' is the quarantined copy of solo itself
    (repo / "quarantine" / "solo.py").write_text("solo = 1\n", encoding="utf-8")
    ddir = tmp_path / "d"
    ddir.mkdir()
    p = rq.plan(repo, _manifest(["solo.py"]), ddir, limit=5)
    assert p["eligible"] == []


# ------------------------------------------------------- token resolution ---

def test_generic_basename_is_identified_by_its_package():
    tok, kind = rq.search_token("services/staged/axis_evidence/__init__.py")
    assert (tok, kind) == ("axis_evidence", "package")
    tok, kind = rq.search_token("services/staged/overview_dashboard/logic.py")
    assert (tok, kind) == ("overview_dashboard", "package")


def test_generic_basename_with_no_useful_package_is_unmeasurable():
    assert rq.search_token("app/__init__.py")[1] == "unmeasurable"
    assert rq.search_token("logic.py")[1] == "unmeasurable"


def test_top_level_module_is_identified_by_its_stem():
    assert rq.search_token("alert_manager.py") == ("alert_manager", "stem")


def test_unmeasurable_is_not_reported_as_zero_references(tmp_path):
    """R6: a candidate we CANNOT measure must land in its own bucket, never in
    'no live referrer'. Reading UNKNOWN as zero is how a live module gets
    classified as dead."""
    repo = tmp_path / "repo"
    (repo / "quarantine" / "app").mkdir(parents=True)
    (repo / "quarantine" / "app" / "__init__.py").write_text("x = 1\n",
                                                             encoding="utf-8")
    ddir = tmp_path / "d"
    ddir.mkdir()
    p = rq.plan(repo, _manifest(["app/__init__.py"]), ddir, limit=5)
    assert p["eligible"] == []
    assert p["skipped"][0]["skip"].startswith("references unmeasurable")
    assert p["skipped"][0]["ref_kind"] == "unmeasurable"


def test_negative_control_generic_token_would_have_ranked_first(tmp_path):
    """The defect this rule exists for, reproduced: search the BARE STEM and
    '__init__' matches every package in the tree, so the least identifiable
    candidate scores the highest and is re-emitted first. With search_token
    the same tree yields no false referrer."""
    repo = tmp_path / "repo"
    (repo / "quarantine" / "services" / "staged" / "axis_evidence").mkdir(parents=True)
    (repo / "quarantine" / "services" / "staged" / "axis_evidence"
     / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    for i in range(5):
        d = repo / ("pkg%d" % i)
        d.mkdir()
        # Any class in the repo writes this. That is the whole problem: the
        # bare stem "__init__" is a Python keyword-shaped token, not an
        # identity, and 2,800 files in zo-sentinel contain it.
        (d / "thing.py").write_text(
            "class Unrelated:\n    def __init__(self):\n        pass\n",
            encoding="utf-8")
    ddir = tmp_path / "d"
    ddir.mkdir()
    cand = "services/staged/axis_evidence/__init__.py"

    # control: the old rule
    import re as _re
    bare = _re.compile(r"\b__init__\b")
    naive = sum(1 for rel, txt in rq._corpus(repo)
                if rel != cand and bare.search(txt))
    assert naive >= 5, "control must reproduce the false-positive storm"

    # cure: the shipped rule finds no referrer for the same tree
    ev = rq.reference_counts(repo, [cand])[cand]
    assert ev["kind"] == "package" and ev["token"] == "axis_evidence"
    assert ev["refs"] == []


# ------------------------------------------------------------- rate limit ---

def test_limit_is_respected(world):
    p = rq.plan(world["repo"], world["manifest"], world["ddir"], limit=1)
    assert len(p["batch"]) == 1
    assert len(p["eligible"]) == 2          # eligible is unclipped; batch is clipped


def test_limit_above_hard_cap_is_clamped(world):
    p = rq.plan(world["repo"], world["manifest"], world["ddir"], limit=999)
    assert p["caps"]["limit"] == rq.MAX_PER_RUN
    assert len(p["batch"]) <= rq.MAX_PER_RUN


def test_in_flight_cap_blocks_emission(world):
    """MAX_IN_FLIGHT outstanding re-emission directives -> headroom 0."""
    for i in range(rq.MAX_IN_FLIGHT):
        (world["ddir"] / ("%sother_%d.json" % (rq.ID_PREFIX, i))).write_text(
            "{}", encoding="utf-8")
    p = rq.plan(world["repo"], world["manifest"], world["ddir"], limit=5)
    assert p["caps"]["headroom"] == 0
    assert p["batch"] == []
    assert len(p["eligible"]) == 2, "eligibility must not be confused with headroom"


def test_negative_control_in_flight_cap(world):
    """Defeat the cap by one and the SAME world yields a non-empty batch."""
    for i in range(rq.MAX_IN_FLIGHT - 1):
        (world["ddir"] / ("%sother_%d.json" % (rq.ID_PREFIX, i))).write_text(
            "{}", encoding="utf-8")
    p = rq.plan(world["repo"], world["manifest"], world["ddir"], limit=5)
    assert p["caps"]["headroom"] == 1
    assert len(p["batch"]) == 1


# ------------------------------------------------------------ idempotence ---

def test_candidate_back_on_main_is_skipped(world):
    (world["repo"] / "alpha.py").write_text("# regenerated\n", encoding="utf-8")
    p = rq.plan(world["repo"], world["manifest"], world["ddir"], limit=5)
    assert "alpha.py" not in [r["candidate"] for r in p["eligible"]]
    assert any(r["candidate"] == "alpha.py" and "already back" in r["skip"]
               for r in p["skipped"])


def test_existing_directive_is_skipped(world):
    did = rq.directive_id_for("beta.py")
    (world["ddir"] / ("%s.json" % did)).write_text("{}", encoding="utf-8")
    p = rq.plan(world["repo"], world["manifest"], world["ddir"], limit=5)
    assert "beta.py" not in [r["candidate"] for r in p["eligible"]]


def test_done_sentinel_is_skipped(world):
    did = rq.directive_id_for("beta.py")
    (world["ddir"] / ("%s.done.json" % did)).write_text("{}", encoding="utf-8")
    p = rq.plan(world["repo"], world["manifest"], world["ddir"], limit=5)
    assert "beta.py" not in [r["candidate"] for r in p["eligible"]]


def test_emit_then_replan_is_a_noop(world):
    """The whole point: run it twice, the second run emits nothing new."""
    first = rq.plan(world["repo"], world["manifest"], world["ddir"], limit=5)
    assert len(first["batch"]) == 2
    for row in first["batch"]:
        rq.emit(world["ddir"], rq.build_directive(row), post_to_bus=False)
    second = rq.plan(world["repo"], world["manifest"], world["ddir"], limit=5)
    assert second["batch"] == []
    assert second["eligible"] == []


def test_negative_control_idempotence(world, monkeypatch):
    """Defeat the live-state check and the second run selects the SAME two
    files again -- i.e. without the guard this tool re-emits forever."""
    for row in rq.plan(world["repo"], world["manifest"],
                       world["ddir"], limit=5)["batch"]:
        rq.emit(world["ddir"], rq.build_directive(row), post_to_bus=False)
    monkeypatch.setattr(rq, "existing_directive_paths", lambda d, i: [])
    monkeypatch.setattr(rq, "already_back", lambda r, c: False)
    again = rq.plan(world["repo"], world["manifest"], world["ddir"], limit=5)
    assert len(again["batch"]) == 2, "guard defeated -> duplicates must reappear"


def test_emit_refuses_to_overwrite(world):
    row = rq.plan(world["repo"], world["manifest"], world["ddir"], limit=1)["batch"][0]
    rq.emit(world["ddir"], rq.build_directive(row), post_to_bus=False)
    with pytest.raises(FileExistsError):
        rq.emit(world["ddir"], rq.build_directive(row), post_to_bus=False)


# -------------------------------------------------------- directive shape ---

def test_directive_ids_are_unique_across_shared_basenames():
    a = rq.directive_id_for("services/staged/one/logic.py")
    b = rq.directive_id_for("services/staged/two/logic.py")
    assert a != b, "a shared basename must not become a shared id"


def test_directive_carries_the_phantom_tables_as_a_prohibition(world):
    row = rq.plan(world["repo"], world["manifest"], world["ddir"], limit=1)["batch"][0]
    d = rq.build_directive(row)
    assert d["handler"] == "generate_file"
    assert d["output_file"] == row["candidate"]
    assert "mcp_servers" in d["description"]
    assert "do NOT reintroduce" in d["description"]
    # the quarantined original is attached so the engine knows what to rebuild
    assert any(r.startswith("quarantine/") for r in d["reads"])
    # names a real table -> _schema_ground_context can match and inline schema
    assert d["reemission"]["issue"] == 4079


def test_emitted_file_is_valid_json_named_for_its_id(world):
    row = rq.plan(world["repo"], world["manifest"], world["ddir"], limit=1)["batch"][0]
    path = rq.emit(world["ddir"], rq.build_directive(row), post_to_bus=False)
    assert path.name == "%s.json" % row["directive_id"]
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["directive_id"] == row["directive_id"]
    assert body["injected_at"].endswith("+00:00")


# ------------------------------------------------------------- manifest -----

def test_bad_manifest_exits_2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert rq.main(["--manifest", str(bad)]) == 2


def test_missing_manifest_exits_2(tmp_path):
    assert rq.main(["--manifest", str(tmp_path / "nope.json")]) == 2


def test_manifest_without_candidates_exits_2(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"files": []}), encoding="utf-8")
    assert rq.main(["--manifest", str(p)]) == 2


def test_real_manifest_parses_and_selects_nothing_dangerous(world):
    """The SHIPPED manifest, against the SHIPPED repo, must plan a batch that
    is small and non-empty-or-explained -- not 69."""
    man = rq.load_manifest(rq.DEFAULT_MANIFEST)
    assert len(man["re_emission"]["candidates"]) == 69
    p = rq.plan(REPO, man, world["ddir"], limit=3)
    assert len(p["batch"]) <= 3
    # Measured against 35d6434 on 2026-09-03: 1 already back, 51 with no live
    # referrer, 0 unmeasurable once packages identify themselves, 17 eligible.
    # The bar asserted here is the PROPERTY -- most of the 69 are filtered and
    # the batch is a handful -- not the exact split, which moves as main moves.
    assert len(p["skipped"]) >= 45, "most of the 69 must be filtered out"
    assert len(p["eligible"]) < 30, "a batch of 69 is what this tool prevents"
    for r in p["batch"]:
        assert r["ref_kind"] in ("stem", "package")
        assert r["ref_count"] > 0


def test_dry_run_writes_nothing(world, tmp_path, capsys):
    man = tmp_path / "m.json"
    man.write_text(json.dumps(world["manifest"]), encoding="utf-8")
    rc = rq.main(["--manifest", str(man), "--repo", str(world["repo"]),
                  "--directives-dir", str(world["ddir"])])
    assert rc == 0
    assert list(world["ddir"].glob("*.json")) == []
