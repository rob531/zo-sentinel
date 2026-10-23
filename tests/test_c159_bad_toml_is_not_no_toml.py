"""c159: a registration that EXISTS and is CORRUPT must not report as ABSENT.

Measured on the LIVE build host (/home/workspace/zo_sentinel, not a repo path -- R1)
on 2026-09-30:

    services/active         614 dirs, 72 carry service.toml
    generate_spine --strict rc=1, 542 unlisted broken, 538 of them NO_TOML
    of the 72 toml files    1 does NOT parse  (perspective_diff_api)
                            1 parses with no [service] table (active_service)

Both of those surfaced as NO_TOML -- the same value as "this directory was never
registered at all" -- because three helpers in tools/generate_spine.py swallowed
their error and returned an empty container:

    _read            except OSError        -> ""     (unreadable module == empty)
    _load_toml       except OSError/Value  -> {}     (corrupt toml == no toml)
    load_known_issues except OSError/Value -> {}     (unloadable allowlist == no
                                                      known issues, silently)

That is HARNESS_DOCTRINE R6 (unknown is not zero) three times in one file, and the
remedies for the conflated cases are opposite: NO_TOML wants a registration
written, BAD_TOML wants the one already there repaired. perspective_diff_api was
reported as unregistered for weeks while its service.toml sat on disk with the
emitter's directive prose block appended after the [service] table.

NEGATIVE CONTROL (R4): test_negative_control_old_code_conflates execs the
origin/main copy of the module -- the pre-fix bytes -- against the same fixture
and asserts it DOES collapse all three into NO_TOML. If the cure is ever reverted
that test fails, so this suite has been observed RED on the code it replaces.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools import generate_spine as G  # noqa: E402

# The real shape of the live defect: the emitter appended the directive prose
# block after the [service] table, so line 11 has no "=" and tomllib refuses it.
CORRUPT_TOML = (
    '[service]\n'
    'name = "perspective_diff_api"\n'
    'import_path = "okmod"\n'
    'prefix = "/api"\n'
    '\n'
    '# Emitted by tools/service_decomposer.py.\n'
    '\n'
    'DATA ACCESS: data lives in databases, never files (no CSV/JSON inputs).\n'
)
OK_MODULE = (
    "from fastapi import APIRouter\n"
    "router = APIRouter(prefix=\"/api\")\n"
    "@router.get(\"/healthy\")\n"
    "def healthy():\n"
    "    return {}\n"
)


def _fixture_tree(tmp_path: Path) -> Path:
    """A root with one of each population, and nothing else."""
    (tmp_path / "okmod.py").write_text(OK_MODULE, encoding="utf-8")
    active = tmp_path / "services" / "active"
    (active / "never_registered").mkdir(parents=True)
    d = active / "corrupt_toml"
    d.mkdir()
    (d / "service.toml").write_text(CORRUPT_TOML, encoding="utf-8")
    d = active / "no_service_table"
    d.mkdir()
    (d / "service.toml").write_text('[other]\nk = 1\n', encoding="utf-8")
    d = active / "healthy"
    d.mkdir()
    (d / "service.toml").write_text(
        '[service]\nname = "healthy"\nimport_path = "okmod"\nprefix = "/api"\n',
        encoding="utf-8")
    return active


@pytest.fixture()
def spine_at(tmp_path, monkeypatch):
    active = _fixture_tree(tmp_path)
    known = tmp_path / "spine_known_issues.json"
    known.write_text(json.dumps({"known": []}), encoding="utf-8")
    monkeypatch.setattr(G, "ROOT", str(tmp_path))
    monkeypatch.setattr(G, "ACTIVE_DIR", str(active))
    monkeypatch.setattr(G, "KNOWN_ISSUES_PATH", str(known))
    return tmp_path, active, known


def _statuses(mod=G):
    return {e["name"]: e["status"] for e in mod.validate(mod.scan_active())}


def test_four_populations_get_four_statuses(spine_at):
    st = _statuses()
    assert st["never_registered"] == "NO_TOML"
    assert st["no_service_table"] == "NO_SERVICE_TABLE"
    assert st["healthy"] == "ok"
    # the corrupt file names itself, not the directory, only when it parsed --
    # it did not, so the entry keeps the directory name.
    assert st["corrupt_toml"] == "BAD_TOML"
    assert len({st["never_registered"], st["no_service_table"], st["corrupt_toml"]}) == 3


def test_bad_toml_carries_the_parser_message(spine_at):
    rec = [e for e in G.validate(G.scan_active()) if e["name"] == "corrupt_toml"][0]
    assert rec["toml_error"], "BAD_TOML with no error text is still a swallow"
    assert "TOMLDecode" in rec["toml_error"] or "Expected" in rec["toml_error"]
    absent = [e for e in G.validate(G.scan_active()) if e["name"] == "never_registered"][0]
    assert not absent["toml_error"]


def test_unreadable_module_is_not_a_missing_router(spine_at, monkeypatch):
    """_read returning "" made an unreadable module look like one with no router."""
    def boom(path, *a, **k):
        if path.endswith("okmod.py"):
            raise OSError(13, "Permission denied")
        return _real_open(path, *a, **k)

    _real_open = open
    monkeypatch.setattr("builtins.open", boom)
    st = _statuses()
    assert st["healthy"] == "UNREADABLE", st


def test_unloadable_allowlist_exits_2_not_a_verdict(spine_at):
    _tmp, _active, known = spine_at
    known.write_text("{not json", encoding="utf-8")
    with pytest.raises(G.KnownIssuesUnreadable):
        G.load_known_issues()
    rc = G.main(["--strict", "--quiet"])
    assert rc == 2, "an allowlist that did not load must never yield 0 or a plain 1"


def test_strict_still_exits_1_on_real_unlisted_debt(spine_at):
    assert G.main(["--strict", "--quiet"]) == 1


# --------------------------------------------------------------------------
# NEGATIVE CONTROL -- the pre-fix bytes, on the same fixture, must go RED.
# --------------------------------------------------------------------------

# The pre-fix bytes of tools/generate_spine.py, PINNED BY BLOB HASH.
#
# 2026-09-30 (cycle-0160): this control originally read
# `origin/main:tools/generate_spine.py`. origin/main is a MOVING REF, and the
# cure merged into it as #5773 (e9557bd12) -- so from that moment the control
# loaded the CURED code and asserted it still conflates. It inverted from
# "proves the defect was real" to "fails forever", and nobody saw it, because
# this file was never collected by the required pytest check (192 test files on
# disk, 71 collected). A negative control aimed at a moving ref stops being a
# control the instant its own fix lands.
#
# A blob hash is immutable, so this loads the same bytes in a year. It is
# e9557bd12^:tools/generate_spine.py -- verified to contain zero BAD_TOML
# occurrences, where the cured file has three.
PRE_FIX_BLOB = "58d3b6f30031f96bd429e8fb83f048b1805bf8b7"


def _load_old_module(tmp_path):
    """The PRE-FIX copy of tools/generate_spine.py, loaded under its own name."""
    blob = subprocess.run(
        ["git", "cat-file", "blob", PRE_FIX_BLOB],
        cwd=str(REPO_ROOT), capture_output=True, timeout=120)
    if blob.returncode != 0:
        pytest.skip(f"pre-fix blob {PRE_FIX_BLOB[:12]} unreachable in this checkout")
    if b"BAD_TOML" in blob.stdout:
        # The control is only a control if these bytes lack the cure. If this
        # ever trips, the hash is wrong -- fail loudly rather than "prove" that
        # cured code conflates.
        raise AssertionError(
            f"blob {PRE_FIX_BLOB[:12]} CONTAINS BAD_TOML -- it is not the "
            f"pre-fix artifact, so this negative control measures nothing")
    old = tmp_path / "generate_spine_OLD.py"
    old.write_bytes(blob.stdout)
    spec = importlib.util.spec_from_file_location("generate_spine_OLD", str(old))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["generate_spine_OLD"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_negative_control_old_code_conflates(spine_at):
    tmp_path, active, known = spine_at
    old = _load_old_module(tmp_path)
    if not hasattr(old, "_load_toml"):
        pytest.skip("origin/main copy has an unexpected shape")
    old.ROOT = str(tmp_path)
    old.ACTIVE_DIR = str(active)
    old.KNOWN_ISSUES_PATH = str(known)
    st = _statuses(old)
    # This is the defect, asserted positively so the control cannot silently pass:
    assert st["never_registered"] == "NO_TOML"
    assert st["corrupt_toml"] == "NO_TOML", "old code should conflate"
    assert st["no_service_table"] == "NO_TOML", "old code should conflate"
    assert not hasattr(old, "KnownIssuesUnreadable")
    # and the old allowlist swallow: corrupt file -> {} -> a plain verdict, not rc=2
    known.write_text("{not json", encoding="utf-8")
    assert old.load_known_issues() == {}
    assert old.main(["--strict", "--quiet"]) == 1


def test_a_lone_healthy_service_is_ok(tmp_path, monkeypatch):
    """Regression on the cure itself.

    The first draft of the new validate() left `status` unbound on the
    readable-module path and only appeared to work because the previous loop
    iteration had leaked a value into it. A fixture with several entries hid
    that; a tree with exactly ONE entry is the negative control for it, and it
    raised UnboundLocalError until the branch was restructured.
    """
    (tmp_path / "okmod.py").write_text(OK_MODULE, encoding="utf-8")
    active = tmp_path / "services" / "active"
    d = active / "healthy"
    d.mkdir(parents=True)
    (d / "service.toml").write_text(
        '[service]\nname = "healthy"\nimport_path = "okmod"\nprefix = "/api"\n',
        encoding="utf-8")
    known = tmp_path / "spine_known_issues.json"
    known.write_text(json.dumps({"known": []}), encoding="utf-8")
    monkeypatch.setattr(G, "ROOT", str(tmp_path))
    monkeypatch.setattr(G, "ACTIVE_DIR", str(active))
    monkeypatch.setattr(G, "KNOWN_ISSUES_PATH", str(known))
    assert _statuses() == {"healthy": "ok"}
    assert G.main(["--strict", "--quiet"]) == 0
