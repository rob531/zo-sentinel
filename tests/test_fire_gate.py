"""Tests for tools/fire_gate.py.

Deliberately built on the REAL Dockerfile shape of this repo (multi-operand COPY of
root modules, a backslash continuation, directory COPYs) rather than a toy one-liner --
the continuation form is the case that would silently drop ~17 modules from the surface
and turn a RESTAGE into a false SAFE.

Every assertion here has been seen to FAIL against a deliberately wrong implementation;
an assertion never seen red is not evidence.
"""

import importlib.util
import json
import pathlib
import sys

import pytest

_MOD_PATH = pathlib.Path(__file__).resolve().parents[1] / "tools" / "fire_gate.py"
_spec = importlib.util.spec_from_file_location("fire_gate", _MOD_PATH)
fire_gate = importlib.util.module_from_spec(_spec)
sys.modules["fire_gate"] = fire_gate
_spec.loader.exec_module(fire_gate)


REAL_SHAPE = """\
FROM python:3.11-slim AS base
WORKDIR /srv
COPY app/requirements.txt /srv/app/requirements.txt
RUN pip install -r /srv/app/requirements.txt
COPY app /srv/app
COPY verdict_breakdown_api.py server_compare_api.py trust_gating_override.py /srv/
COPY facet_enum_service.py perspective_model.py \\
     ask_answer_api.py vuln_identity.py /srv/
COPY zo_sentinel/__init__.py zo_sentinel/policy.py zo_sentinel/policy_defaults.toml /srv/zo_sentinel/
COPY perspective_tree_view.html scan_view.html /srv/
COPY migrations /srv/migrations
COPY alembic.ini /srv/alembic.ini
COPY --from=frontend /build/dist /srv/app/static
CMD ["uvicorn", "app.main:app"]
"""


@pytest.fixture()
def surface():
    srcs = fire_gate.copy_sources(REAL_SHAPE)
    return srcs, *fire_gate.build_surface(srcs)


def test_backslash_continuation_is_spliced_not_dropped(surface):
    srcs, _, _ = surface
    # vuln_identity.py sits AFTER the continuation; a naive line-wise parser loses it.
    assert "vuln_identity.py" in srcs
    assert "ask_answer_api.py" in srcs
    assert "facet_enum_service.py" in srcs


def test_destination_operand_is_never_treated_as_a_source(surface):
    srcs, files, prefixes = surface
    assert "/srv/" not in srcs and "/srv/migrations" not in srcs
    assert not any(f.startswith("srv") for f in files)


def test_from_stage_copies_are_ignored(surface):
    srcs, _, _ = surface
    # inputs come from a build stage, not the git tree, so no repo path can change them
    assert not any("/build/dist" in s for s in srcs)


def test_directory_copies_become_prefixes(surface):
    _, files, prefixes = surface
    assert "app/" in prefixes
    assert "migrations/" in prefixes
    assert "alembic.ini" in files


@pytest.mark.parametrize(
    "path",
    [
        "app/main.py",                      # COPYed dir
        "migrations/versions/0011_x.py",    # COPYed dir
        "vuln_identity.py",                 # COPYed file behind a continuation
        "zo_sentinel/policy.py",            # COPYed file, nested
        "alembic.ini",                      # COPYed file
        "Dockerfile",                       # defines the COPY list
        ".dockerignore",                    # silently subtracts from every COPY
        "fly.toml",                         # release_command / health checks
        "services/active/foo.toml",         # promotion surface (FU-102 / v64 class)
    ],
)
def test_image_surface_paths_force_restage(path, surface):
    _, files, prefixes = surface
    assert fire_gate.classify(path, files, prefixes) is not None, path


@pytest.mark.parametrize(
    "path",
    [
        "tools/run_verify.ps1",
        "tools/fire_gate.py",
        ".github/workflows/copilot-autofix-commit.yml",
        "tests/test_fire_gate.py",
        "docs/AUTOPOIESIS.md",
        "services/staged/scaffold_thing/__init__.py",
    ],
)
def test_paths_that_cannot_reach_the_image_stay_safe(path, surface):
    _, files, prefixes = surface
    assert fire_gate.classify(path, files, prefixes) is None, path


def test_staged_beats_active_prefix_ordering(surface):
    """services/staged/ must stay inert even though services/active/ is sensitive."""
    _, files, prefixes = surface
    assert fire_gate.classify("services/staged/x/y.py", files, prefixes) is None
    assert fire_gate.classify("services/active/x.toml", files, prefixes) is not None


def test_copy_dot_makes_the_whole_tree_the_surface():
    srcs = fire_gate.copy_sources("FROM x\nCOPY . /srv/\n")
    files, prefixes = fire_gate.build_surface(srcs)
    assert "" in prefixes
    assert fire_gate.classify("anything/at/all.py", files, prefixes) is not None
    # ...but the staged scratch surface is still explicitly inert
    assert fire_gate.classify("services/staged/x.py", files, prefixes) is None


def test_zero_copy_sources_is_an_error_not_a_pass():
    """A Dockerfile we failed to parse must never yield an empty (=permissive) surface."""
    assert fire_gate.copy_sources("FROM python:3.11\nRUN echo hi\n") == []


# --- FU-160: the target head must be RESOLVED, not read off page one -----------------
#
# Compare pages commits 100 at a time. The original implementation took the head from
# the last commit of the FIRST page, so any delta over 100 commits named the 100th
# commit as "the target head" -- observed live on a 124-commit delta. The verdict was
# right and the evidence was wrong, which is the harder failure to notice.

_HEAD = "b" * 40
_PAGE1_LAST = "a" * 40


def _fake_gh(compare_docs, head=_HEAD):
    """Stand in for fire_gate._gh, dispatching on the endpoint being called."""

    def _gh(args):
        endpoint = args[1] if len(args) > 1 else ""
        if "/commits/" in endpoint:
            return head + "\n"
        if "/compare/" in endpoint:
            return "".join(json.dumps(d) for d in compare_docs)
        raise AssertionError(f"unexpected gh call: {args}")

    return _gh


def _two_page_compare(nfiles=3):
    """Page 1 ends at a commit that is NOT the head; page 2 carries no files."""
    files = [{"filename": f"services/staged/s{i}.py"} for i in range(nfiles)]
    return [
        {"total_commits": 124, "commits": [{"sha": _PAGE1_LAST}] * 100, "files": files},
        {"total_commits": 124, "commits": [{"sha": _HEAD}] * 24, "files": []},
    ]


def test_head_comes_from_the_commits_api_not_from_page_one(monkeypatch):
    monkeypatch.setattr(fire_gate, "_gh", _fake_gh(_two_page_compare()))
    assert fire_gate.resolve_head("r/r", "main") == _HEAD
    assert fire_gate.resolve_head("r/r", "main") != _PAGE1_LAST


def test_compare_is_pinned_to_the_resolved_sha_not_the_ref(monkeypatch):
    """The sha we NAME must be the sha we JUDGED -- no ref left to move mid-run."""
    seen = []

    def _gh(args):
        seen.append(args[1])
        if "/commits/" in args[1]:
            return _HEAD + "\n"
        return "".join(json.dumps(d) for d in _two_page_compare())

    monkeypatch.setattr(fire_gate, "_gh", _gh)
    head = fire_gate.resolve_head("r/r", "main")
    fire_gate.changed_files("r/r", "c" * 40, head)
    compares = [e for e in seen if "/compare/" in e]
    assert len(compares) == 1
    assert compares[0].endswith(_HEAD), f"compare was not pinned to the resolved sha: {compares[0]}"


def test_files_across_all_pages_are_merged(monkeypatch):
    monkeypatch.setattr(fire_gate, "_gh", _fake_gh(_two_page_compare(nfiles=5)))
    changed, ncommits, _src = fire_gate.changed_files("r/r", "c" * 40, _HEAD)
    assert ncommits == 124
    assert len(changed) == 5


def test_hitting_the_file_cap_is_an_error_not_a_verdict(monkeypatch):
    """A truncated file list cannot support SAFE -- and looks exactly like a full one."""
    docs = [{
        "total_commits": 900,
        "commits": [{"sha": _HEAD}],
        "files": [{"filename": f"f{i}.py"} for i in range(fire_gate.COMPARE_FILES_CAP)],
    }]
    monkeypatch.setattr(fire_gate, "_gh", _fake_gh(docs))
    with pytest.raises(RuntimeError, match="cap"):
        fire_gate.changed_files("r/r", "c" * 40, _HEAD)


def test_under_the_cap_still_returns_normally(monkeypatch):
    docs = [{
        "total_commits": 10,
        "commits": [{"sha": _HEAD}],
        "files": [{"filename": f"f{i}.py"} for i in range(fire_gate.COMPARE_FILES_CAP - 1)],
    }]
    monkeypatch.setattr(fire_gate, "_gh", _fake_gh(docs))
    changed, _n, _src = fire_gate.changed_files("r/r", "c" * 40, _HEAD)
    assert len(changed) == fire_gate.COMPARE_FILES_CAP - 1


def test_an_unresolvable_target_is_an_error(monkeypatch):
    monkeypatch.setattr(fire_gate, "_gh", _fake_gh([], head="not-a-sha"))
    with pytest.raises(RuntimeError, match="resolve"):
        fire_gate.resolve_head("r/r", "no-such-branch")


# --- FU-170: the EXIT CODE is the interface, and it had never been seen RED ----------
#
# Every assertion above exercises classify()/copy_sources()/changed_files() in
# ISOLATION. But the thing prod-drift-sentinel and the chairman actually read is
# main()'s EXIT CODE -- 0 SAFE, 1 RESTAGE, 2 ERROR. Nothing in this suite asserted that
# main() ever RETURNS 1. An implementation that computed `hits` perfectly and then ended
# `return 0` would have passed all 28 tests while converting every RESTAGE into a false
# SAFE -- and fire_gate's SAFE is the single assertion that lets the sentinel keep an
# 18-run-old stage valid without re-verifying. That is the "guard never seen red" class.
#
# Observed live 2026-07-29T13:56Z against the real repo:
#   --staged f63bcb155d0a8f2a35060f686c322224cfe99e0e
#   --target c02fa1350b5a06b137e42f945afc649a4624e212
#   -> rc=1, RESTAGE, naming app/scoring_consumer.py [COPY (dir app/)]
# These tests pin that end-to-end behaviour so it never has to be re-derived by hand.

import base64


def _fake_gh_full(dockerfile, compare_docs, head=_HEAD):
    """Like _fake_gh, but also serves the Dockerfile blob main() fetches first."""

    def _gh(args):
        endpoint = args[1] if len(args) > 1 else ""
        if "contents/Dockerfile" in endpoint:
            return base64.b64encode(dockerfile.encode()).decode()
        if "/commits/" in endpoint:
            return head + "\n"
        if "/compare/" in endpoint:
            return "".join(json.dumps(d) for d in compare_docs)
        raise AssertionError(f"unexpected gh call: {args}")

    return _gh


def _run_main(monkeypatch, files, dockerfile=REAL_SHAPE):
    docs = [{
        "total_commits": 3,
        "commits": [{"sha": _HEAD}],
        "files": [{"filename": f} for f in files],
    }]
    monkeypatch.setattr(fire_gate, "_gh", _fake_gh_full(dockerfile, docs))
    monkeypatch.setattr(sys, "argv", ["fire_gate.py", "--staged", "c" * 40])
    return fire_gate.main()


def test_main_returns_1_when_the_delta_reaches_the_image(monkeypatch):
    """The live negative control, pinned. This is the assertion that was missing."""
    assert _run_main(monkeypatch, ["app/scoring_consumer.py"]) == 1


def test_main_returns_0_only_when_nothing_reaches_the_image(monkeypatch):
    inert = ["tools/x.py", "services/staged/y.py", ".github/workflows/z.yml", "docs/a.md"]
    assert _run_main(monkeypatch, inert) == 0


def test_one_reaching_path_among_many_inert_ones_still_restages(monkeypatch):
    """The hit must not be diluted by the inert majority -- FU-160's real shape."""
    mixed = ["tools/x.py", "services/staged/y.py", "alembic.ini", "docs/a.md"]
    assert _run_main(monkeypatch, mixed) == 1


@pytest.mark.parametrize(
    "path", ["Dockerfile", ".dockerignore", "fly.toml", "services/active/x.toml"]
)
def test_contract_paths_exit_restage_through_main(monkeypatch, path):
    assert _run_main(monkeypatch, [path]) == 1


def test_main_returns_2_and_never_0_when_it_cannot_evaluate(monkeypatch):
    """A probe that cannot evaluate is not a green."""

    def _boom(args):
        raise RuntimeError("compare returned 300 files, at or over the cap")

    monkeypatch.setattr(fire_gate, "_gh", _boom)
    monkeypatch.setattr(sys, "argv", ["fire_gate.py", "--staged", "c" * 40])
    assert fire_gate.main() == 2


def test_an_unparseable_dockerfile_is_an_error_not_a_safe(monkeypatch):
    """Zero COPY sources => empty surface => every path looks inert. Must be ERROR."""
    rc = _run_main(monkeypatch, ["app/main.py"], dockerfile="FROM python:3.11\nRUN echo hi\n")
    assert rc == 2


# --------------------------------------------------------------------------- CI
# Added 2026-08-05 (improvement-loop cycle-0006) with sha_green wired in. The whole
# value of the asymmetry is that RED and UNKNOWN behave DIFFERENTLY, so both poles
# are asserted on the same function; each of these has been seen to fail against the
# obvious wrong implementation (`if ci["rc"]: restage`), which reds the pipe on every
# instrument outage.


def test_ci_red_turns_an_image_inert_delta_into_restage():
    """The motivating case: nothing in the delta reaches the image, so the old code
    said SAFE -- about a commit that failed CI."""
    verdict, forced = fire_gate.apply_ci("SAFE", {"verdict": "RED", "rc": 1})
    assert verdict == "RESTAGE"
    assert forced is True


def test_ci_unknown_never_changes_the_verdict():
    """R6/R7. This is the assertion that stops the wiring becoming a gate that can
    stall the pipe whenever GitHub hiccups."""
    for ci in ({"verdict": "UNKNOWN", "rc": 2}, {"verdict": "UNKNOWN", "rc": None},
               {"verdict": "SKIPPED", "rc": None}, {}):
        verdict, forced = fire_gate.apply_ci("SAFE", ci)
        assert verdict == "SAFE", ci
        assert forced is False, ci


def test_ci_green_leaves_a_safe_delta_safe():
    verdict, forced = fire_gate.apply_ci("SAFE", {"verdict": "GREEN", "rc": 0})
    assert verdict == "SAFE"
    assert forced is False


def test_ci_never_rescues_a_restage():
    """A green CI verdict must not launder a delta that touches the image surface --
    the two questions are independent and only one of them is about bytes."""
    for ci in ({"verdict": "GREEN", "rc": 0}, {"verdict": "RED", "rc": 1},
               {"verdict": "UNKNOWN", "rc": 2}):
        verdict, forced = fire_gate.apply_ci("RESTAGE", ci)
        assert verdict == "RESTAGE", ci
        assert forced is False, ci


def test_target_ci_reports_unknown_when_sha_green_cannot_be_consulted(monkeypatch):
    """An import error, a missing gh, an API outage -- all must arrive as UNKNOWN
    with a stated reason, never as a crash and never as a verdict."""
    import builtins
    real_import = builtins.__import__

    def boom(name, *args, **kw):
        if name == "sha_green":
            raise ImportError("simulated: sha_green unavailable")
        return real_import(name, *args, **kw)

    monkeypatch.setattr(builtins, "__import__", boom)
    ci = fire_gate.target_ci("o/r", "0" * 40)
    assert ci["verdict"] == "UNKNOWN"
    assert ci["rc"] == 2
    assert ci["source"] == "unavailable"
    assert "sha_green" in ci["detail"]
    # ...and an UNKNOWN from an unavailable instrument must still not block.
    assert fire_gate.apply_ci("SAFE", ci) == ("SAFE", False)
