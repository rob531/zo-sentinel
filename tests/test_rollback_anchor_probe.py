"""Tests for tools/rollback_anchor_probe.py.

The load-bearing test here is `test_non_discriminating_control_is_unknown`: a probe
that cannot tell a real tag from an impossible one would have printed 200 for the
anchor on the exact day the anchor was gone. Every assertion in this file was run
against a deliberately wrong implementation and seen RED first -- specifically
against a version that dropped the control check entirely (which turns the
all-200 registry case into a false PULLABLE) and against one that mapped 401 to
MISSING (which turns a token expiry into "your rollback image is gone").
"""

import importlib.util
import pathlib
import sys

import pytest

_MOD_PATH = pathlib.Path(__file__).resolve().parents[1] / "tools" / "rollback_anchor_probe.py"
_spec = importlib.util.spec_from_file_location("rollback_anchor_probe", _MOD_PATH)
rap = importlib.util.module_from_spec(_spec)
sys.modules["rollback_anchor_probe"] = rap
_spec.loader.exec_module(rap)


REF = "registry.fly.io/mcplookup:deployment-01KYQEJQJH4Q541KQSN25A3X3J"
TAG = "deployment-01KYQEJQJH4Q541KQSN25A3X3J"


def opener_for(status_by_tag, default=404):
    def _o(url, _token):
        tag = url.rsplit("/", 1)[-1]
        st = status_by_tag.get(tag, default)
        return st, ("sha256:feedface" if st == 200 else "")
    return _o


# ------------------------------------------------------------------- the happy path
def test_present_anchor_is_pullable():
    r = rap.probe(REF, "tok", opener=opener_for({TAG: 200}))
    assert r["rc"] == rap.RC_PULLABLE
    assert r["verdict"] == "PULLABLE"
    assert r["digest"] == "sha256:feedface"
    assert r["control_status"] == 404


# --------------------------------------------------------- the discriminated red
def test_absent_anchor_with_good_control_is_missing():
    r = rap.probe(REF, "tok", opener=opener_for({}))
    assert r["rc"] == rap.RC_MISSING
    assert r["verdict"] == "MISSING"


# ------------------------------------------------------------ THE load-bearing one
def test_non_discriminating_control_is_unknown_never_pullable():
    """A registry that 200s everything proves nothing about the anchor."""
    r = rap.probe(REF, "tok", opener=lambda u, t: (200, "sha256:x"))
    assert r["rc"] == rap.RC_UNKNOWN
    assert r["verdict"] != "PULLABLE"
    assert "not discriminating" in r["reason"]


@pytest.mark.parametrize("code", [401, 403, 429, 500, 503])
def test_control_non_404_is_always_unknown(code):
    r = rap.probe(REF, "tok", opener=lambda u, t: (code, ""))
    assert r["rc"] == rap.RC_UNKNOWN, f"control {code} must not be readable as a verdict"


# ------------------------------------------- cannot-evaluate is neither pass nor fail
def test_auth_failure_is_not_missing():
    """FU-151: flyctl's token timer expiring must not read as a vanished image."""
    r = rap.probe(REF, "tok", opener=lambda u, t: (401, ""))
    assert r["rc"] == rap.RC_UNKNOWN
    assert r["verdict"] != "MISSING"


def test_missing_token_is_unknown_not_pullable():
    r = rap.probe(REF, "", opener=opener_for({TAG: 200}))
    assert r["rc"] == rap.RC_UNKNOWN


def test_transport_failure_is_unknown():
    def boom(_u, _t):
        raise RuntimeError("transport failure: simulated")
    r = rap.probe(REF, "tok", opener=boom)
    assert r["rc"] == rap.RC_UNKNOWN


def test_anchor_5xx_is_unknown_not_missing():
    r = rap.probe(REF, "tok", opener=opener_for({TAG: 500}))
    assert r["rc"] == rap.RC_UNKNOWN
    assert r["verdict"] != "MISSING"


# ---------------------------------------------------------------------- ref parsing
def test_split_ref_real_shape():
    assert rap.split_ref(REF) == ("mcplookup", TAG)


@pytest.mark.parametrize("bad", ["", "mcplookup", "registry.fly.io/mcplookup",
                                 "registry.fly.io/:tag", "no-slash:tag"])
def test_split_ref_rejects_untagged(bad):
    with pytest.raises(ValueError):
        rap.split_ref(bad)


def test_untagged_ref_is_unknown_not_a_crash():
    r = rap.probe("mcplookup", "tok", opener=opener_for({TAG: 200}))
    assert r["rc"] == rap.RC_UNKNOWN


# ------------------------------------------------------------------ control tag is impossible
def test_control_tag_cannot_be_a_real_ulid():
    """Crockford base32 excludes I, L, O, U -- so no real deployment tag can collide."""
    suffix = rap.CONTROL_TAG.split("-", 1)[1]
    assert set(suffix) & set("ILOU"), "control tag must be un-generatable by Fly"


# ------------------------------------------------------------------- anchor resolution
class _Proc:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


RELEASES_JSON = """[
 {"Version": 65, "Status": "complete", "CreatedAt": "2026-07-29T17:26:10Z",
  "ImageRef": "registry.fly.io/mcplookup:deployment-01KYQEJQJH4Q541KQSN25A3X3J"},
 {"Version": 64, "Status": "complete", "CreatedAt": "2026-07-25T21:23:09Z",
  "ImageRef": "registry.fly.io/mcplookup:deployment-01KYDJH25027410YTVK4MQD20P"}
]"""


def test_resolve_anchor_defaults_to_current_release():
    ref, basis = rap.resolve_anchor("mcplookup",
                                    runner=lambda cmd: _Proc(0, RELEASES_JSON))
    assert ref.endswith("01KYQEJQJH4Q541KQSN25A3X3J")
    assert "v65" in basis


def test_resolve_anchor_can_target_an_older_release():
    ref, _ = rap.resolve_anchor("mcplookup", version=64,
                                runner=lambda cmd: _Proc(0, RELEASES_JSON))
    assert ref.endswith("01KYDJH25027410YTVK4MQD20P")


def test_resolve_anchor_raises_on_flyctl_failure():
    with pytest.raises(RuntimeError):
        rap.resolve_anchor("mcplookup",
                           runner=lambda cmd: _Proc(1, "", "no access token available"))


def test_resolve_anchor_raises_on_unknown_version():
    with pytest.raises(RuntimeError):
        rap.resolve_anchor("mcplookup", version=99,
                           runner=lambda cmd: _Proc(0, RELEASES_JSON))


def test_main_maps_unresolvable_ref_to_rc2(capsys):
    rc = rap.main(["--image", "not-a-ref", "--json"])
    assert rc == rap.RC_UNKNOWN


def test_self_test_passes():
    assert rap._self_test() == 0
