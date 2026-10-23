"""Tests for the queue census.

The alarms ARE the product, so every one is asserted firing and — more importantly
— asserted NOT firing on the case it must not fire on. An alarm only ever seen
green is indistinguishable from an alarm that cannot fire, which is how this repo
acquired a workflow that was red on every commit and had never run.

No network: `gh` is never invoked here. collect() is the only function that shells
out, and it is deliberately not under test — everything that makes a JUDGEMENT is
pure and takes its data as an argument.
"""
import datetime as dt
import importlib.util
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CENSUS = os.path.join(ROOT, "tools", "queue_census.py")


def _load():
    spec = importlib.util.spec_from_file_location("queue_census", CENSUS)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["queue_census"] = mod
    spec.loader.exec_module(mod)
    return mod


NOW = dt.datetime(2026, 7, 30, 12, 0, tzinfo=dt.timezone.utc)


def _lane(name="builder:manifest", **kw):
    base = {"name": name, "validator": "service_manifest", "depth": 0,
            "opened_24h": 0, "merged_24h": 0, "valid": 0, "checked": 0,
            "invalid_examples": [], "silent_for": None, "undrained_for": None,
            "validity": None}
    base.update(kw)
    return base


def _snap(*lanes):
    return {"at": NOW.isoformat(), "repo": "x/y", "validated": True,
            "open_total": sum(l["depth"] for l in lanes), "lanes": list(lanes)}


# --------------------------------------------------------------------------
# lane attribution
# --------------------------------------------------------------------------
def test_lane_of_attributes_a_single_file_manifest_pr():
    m = _load()
    pr = {"labels": [{"name": "autonomous-build"}],
          "files": [{"path": "services/staged/foo/service.toml"}]}
    assert m.lane_of(pr) == "builder:manifest"


def test_lane_of_does_not_claim_a_multi_file_pr_for_a_single_file_lane():
    m = _load()
    pr = {"labels": [{"name": "autonomous-build"}],
          "files": [{"path": "services/staged/foo/service.toml"},
                    {"path": "services/staged/foo/router.py"}]}
    assert m.lane_of(pr) == "builder:other"


def test_lane_of_puts_unlabelled_work_in_human_fu():
    m = _load()
    assert m.lane_of({"labels": [], "files": [{"path": "tools/x.py"}]}) == "human/fu"


# --------------------------------------------------------------------------
# VALIDITY_COLLAPSE -- the 2026-07-29 defect
# --------------------------------------------------------------------------
def test_validity_collapse_fires_on_a_lane_emitting_invalid_work():
    m = _load()
    snap = _snap(_lane(depth=36, checked=36, valid=0, validity=0.0, opened_24h=36))
    kinds = [a["kind"] for a in m.alarms(snap, None)]
    assert "VALIDITY_COLLAPSE" in kinds


def test_validity_collapse_does_not_fire_below_the_minimum_cohort():
    """1/1 invalid is a coin flip, not a regression."""
    m = _load()
    snap = _snap(_lane(depth=1, checked=1, valid=0, validity=0.0, opened_24h=1))
    assert "VALIDITY_COLLAPSE" not in [a["kind"] for a in m.alarms(snap, None)]


def test_validity_collapse_does_not_fire_on_a_healthy_lane():
    m = _load()
    snap = _snap(_lane(depth=20, checked=20, valid=20, validity=1.0, opened_24h=20))
    assert "VALIDITY_COLLAPSE" not in [a["kind"] for a in m.alarms(snap, None)]


def test_a_deep_queue_alone_is_not_an_alarm():
    """'36 open' is not a defect. '36 open and 0 valid' is. The whole design."""
    m = _load()
    snap = _snap(_lane(depth=36, checked=36, valid=36, validity=1.0,
                       opened_24h=4, merged_24h=4, undrained_for=1.0))
    assert m.alarms(snap, None) == []


# --------------------------------------------------------------------------
# UNDRAINED -- the 2026-07-30 defect ("no builds for 11 hrs")
# --------------------------------------------------------------------------
def test_undrained_fires_when_emission_continues_but_nothing_merges():
    m = _load()
    snap = _snap(_lane(depth=23, opened_24h=2, merged_24h=0, undrained_for=11.6))
    a = [x for x in m.alarms(snap, None) if x["kind"] == "UNDRAINED"]
    assert a and "11.6" in a[0]["detail"]


def test_undrained_does_not_fire_when_the_lane_is_also_empty():
    """Nothing merged because nothing is waiting. Not a blockage."""
    m = _load()
    snap = _snap(_lane(depth=0, opened_24h=0, merged_24h=0, undrained_for=40.0))
    assert "UNDRAINED" not in [a["kind"] for a in m.alarms(snap, None)]


def test_undrained_does_not_fire_on_a_recently_drained_lane():
    m = _load()
    snap = _snap(_lane(depth=5, opened_24h=5, merged_24h=5, undrained_for=0.5))
    assert "UNDRAINED" not in [a["kind"] for a in m.alarms(snap, None)]


# --------------------------------------------------------------------------
# LANE_SILENT -- requires PRIOR evidence of emission
# --------------------------------------------------------------------------
def test_lane_silent_fires_only_with_prior_evidence_of_emission():
    m = _load()
    snap = _snap(_lane(depth=2, opened_24h=0, silent_for=13.0))
    prev = _snap(_lane(depth=2, opened_24h=9))
    assert "LANE_SILENT" in [a["kind"] for a in m.alarms(snap, prev)]


def test_lane_silent_does_not_fire_for_a_lane_that_never_emitted():
    """Otherwise a dormant lane alarms forever and the census gets ignored."""
    m = _load()
    snap = _snap(_lane(depth=0, opened_24h=0, silent_for=900.0))
    prev = _snap(_lane(depth=0, opened_24h=0))
    assert "LANE_SILENT" not in [a["kind"] for a in m.alarms(snap, prev)]


def test_lane_silent_does_not_fire_on_the_first_ever_census():
    """No prior snapshot means no derivative. Guessing one would fabricate a trend."""
    m = _load()
    snap = _snap(_lane(depth=2, opened_24h=0, silent_for=99.0))
    assert "LANE_SILENT" not in [a["kind"] for a in m.alarms(snap, None)]


# --------------------------------------------------------------------------
# DIVERGING
# --------------------------------------------------------------------------
def test_diverging_fires_when_depth_grows_and_emission_outruns_drain():
    m = _load()
    snap = _snap(_lane(depth=30, opened_24h=20, merged_24h=2, undrained_for=1.0))
    prev = _snap(_lane(depth=10))
    assert "DIVERGING" in [a["kind"] for a in m.alarms(snap, prev)]


def test_diverging_does_not_fire_when_depth_is_shrinking():
    m = _load()
    snap = _snap(_lane(depth=8, opened_24h=20, merged_24h=2, undrained_for=1.0))
    prev = _snap(_lane(depth=30))
    assert "DIVERGING" not in [a["kind"] for a in m.alarms(snap, prev)]


# --------------------------------------------------------------------------
# the declare hatch -- stale and reasonless must NOT suppress
# --------------------------------------------------------------------------
def _decl(entries):
    fd, p = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(entries, fh)
    return p


def test_a_valid_declaration_suppresses_but_is_still_reported():
    m = _load()
    p = _decl([{"lane": "builder:other", "kind": "UNDRAINED",
                "reason": "chairman ruling 7/30: do not bulk-merge",
                "expires": "2026-08-06"}])
    d = m.load_declared(p, NOW)
    assert len(d["active"]) == 1
    snap = _snap(_lane("builder:other", depth=23, opened_24h=2, merged_24h=0,
                       undrained_for=11.6))
    live, supp = m.apply_declared(m.alarms(snap, None), d)
    assert live == [] and len(supp) == 1
    assert supp[0]["declared_reason"].startswith("chairman ruling")


def test_an_expired_declaration_does_not_suppress():
    m = _load()
    p = _decl([{"lane": "builder:other", "kind": "UNDRAINED",
                "reason": "temporary", "expires": "2026-07-01"}])
    d = m.load_declared(p, NOW)
    assert d["stale"] and not d["active"]
    snap = _snap(_lane("builder:other", depth=23, opened_24h=2, merged_24h=0,
                       undrained_for=11.6))
    live, supp = m.apply_declared(m.alarms(snap, None), d)
    assert len(live) == 1 and supp == []


def test_a_reasonless_declaration_does_not_suppress():
    m = _load()
    p = _decl([{"lane": "builder:other", "kind": "UNDRAINED", "expires": "2026-08-06"}])
    d = m.load_declared(p, NOW)
    assert d["reasonless"] and not d["active"]


def test_a_declaration_with_no_expiry_is_treated_as_reasonless():
    """An unexpiring suppression is a permanent blindfold nobody remembers."""
    m = _load()
    p = _decl([{"lane": "builder:other", "kind": "UNDRAINED", "reason": "because"}])
    d = m.load_declared(p, NOW)
    assert d["reasonless"] and not d["active"]


def test_a_missing_declarations_file_is_not_an_error():
    m = _load()
    d = m.load_declared(os.path.join(tempfile.gettempdir(), "nope-does-not-exist.json"))
    assert d == {"active": [], "stale": [], "reasonless": []}


def test_declaration_is_scoped_to_one_lane_and_one_kind():
    """A blanket silence would take out the alarms it was never meant to cover."""
    m = _load()
    p = _decl([{"lane": "builder:other", "kind": "UNDRAINED",
                "reason": "ruling", "expires": "2026-08-06"}])
    d = m.load_declared(p, NOW)
    snap = _snap(_lane("builder:other", depth=23, opened_24h=2, merged_24h=0,
                       undrained_for=11.6),
                 _lane("builder:router", depth=9, opened_24h=2, merged_24h=0,
                       undrained_for=11.6))
    live, supp = m.apply_declared(m.alarms(snap, None), d)
    assert [a["lane"] for a in live] == ["builder:router"]
    assert [a["lane"] for a in supp] == ["builder:other"]


# --------------------------------------------------------------------------
# validators
# --------------------------------------------------------------------------
def test_python_validator_rejects_a_hollow_stub(monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "_added_lines", lambda n: "pass\n")
    ok, why = m._v_python_syntax(1, ["services/staged/x/router.py"])
    assert not ok and "hollow" in why


def test_python_validator_rejects_a_syntax_error(monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "_added_lines", lambda n: "def f(:\n  pass\n  return 1\n")
    ok, why = m._v_python_syntax(1, ["services/staged/x/router.py"])
    assert not ok and "SyntaxError" in why


def test_python_validator_accepts_real_code(monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "_added_lines",
                        lambda n: "import os\n\n\ndef f():\n    return os.sep\n")
    ok, _ = m._v_python_syntax(1, ["services/staged/x/router.py"])
    assert ok


def test_manifest_validator_shares_the_ci_gates_code(monkeypatch):
    """Not a second copy of the rule -- the same classify_source the gate runs."""
    m = _load()
    flat = ('name = "foo"\nimport_path = "services.active.foo.router"\n'
            'prefix = "/api"\ntag = "foo"\n')
    monkeypatch.setattr(m, "_added_lines", lambda n: flat)
    ok, why = m._v_service_manifest(1, ["services/staged/foo/service.toml"])
    assert not ok and why.startswith("FLAT")

    good = "[service]\n" + flat
    monkeypatch.setattr(m, "_added_lines", lambda n: good)
    ok, _ = m._v_service_manifest(1, ["services/staged/foo/service.toml"])
    assert ok


def test_an_unreadable_diff_is_invalid_not_valid(monkeypatch):
    """Fail closed: a validator that cannot read its subject must not bless it."""
    m = _load()
    monkeypatch.setattr(m, "_added_lines", lambda n: "")
    assert not m._v_service_manifest(1, ["services/staged/foo/service.toml"])[0]
    assert not m._v_python_syntax(1, ["services/staged/foo/router.py"])[0]


# --------------------------------------------------------------------------
# the ARMED halt's founding case -- cycle-0032
#
# `queue_census --halt-mode` has defaulted to ARMED since 2026-07-30, and it was
# armed on the strength of tools/halt_shadow_report.py showing the 2026-07-29
# incident still reproducing. That report then had NO CALLER of any kind: nothing
# re-asked the question after the actuator went hot, so a later loosening of
# VALIDITY_FLOOR or MIN_COHORT would have silently un-armed the halt against the
# only incident it is known to catch, and every surface would have stayed green.
#
# The report's own docstring says that if the thresholds ever move past this
# incident "THIS REPORT GOES QUIET and that is the signal". These tests are the
# receiver for that signal. They run the REAL alarm code through the report's own
# retrospect(), so a loosened threshold and an edited fixture both surface here.
# --------------------------------------------------------------------------
SHADOW = os.path.join(ROOT, "tools", "halt_shadow_report.py")


def _load_shadow():
    spec = importlib.util.spec_from_file_location("halt_shadow_report", SHADOW)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["halt_shadow_report"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_armed_halt_still_fires_on_its_founding_case():
    m = _load()
    fired = _load_shadow().retrospect(m)
    assert [a["kind"] for a in fired] == ["VALIDITY_COLLAPSE"]
    assert fired[0]["lane"] == "builder:manifest"


def test_the_founding_case_is_bound_to_the_live_validity_floor(monkeypatch):
    """NEGATIVE CONTROL, kept permanently rather than run once.

    Drop VALIDITY_FLOOR to 0 and the founding case must STOP firing. If this test
    cannot be made to fail, the one above is asserting nothing.
    """
    m = _load()
    monkeypatch.setattr(m, "VALIDITY_FLOOR", 0.0)
    assert _load_shadow().retrospect(m) == []


def test_the_founding_case_is_bound_to_the_live_min_cohort(monkeypatch):
    """The second way to silently un-arm the halt: raise the cohort floor above the
    incident's own cohort (36) and the collapse becomes unjudgeable, not absent."""
    m = _load()
    monkeypatch.setattr(m, "MIN_COHORT", 50)
    assert _load_shadow().retrospect(m) == []
