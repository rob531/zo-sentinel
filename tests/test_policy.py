"""Tests for zo_sentinel.policy -- the declarative operational policy layer.

Covers the full precedence chain (env > durable override > legacy sentinel >
policy_defaults.toml > embedded), explicit-off sentinel semantics, fail-open
on malformed layers, migration, atomic set/unset, and that the three module
gates actually resolve through policy.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from zo_sentinel import policy  # noqa: E402
from zo_sentinel import anchor_refill, engine_build, queue_janitor  # noqa: E402


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """Isolated policy environment: tmp override file, tmp directives root,
    no gate env vars leaking in."""
    override = tmp_path / "state" / "policy_override.json"
    monkeypatch.setenv("ZO_POLICY_OVERRIDE_PATH", str(override))
    directives = tmp_path / "directives"
    directives.mkdir()
    for meta in policy.KEYS.values():
        if meta.get("env"):
            monkeypatch.delenv(meta["env"], raising=False)
    return {"override": override, "directives": directives}


# ---------------------------------------------------------------------------
# Precedence chain
# ---------------------------------------------------------------------------

def test_tracked_defaults_are_the_declared_posture(iso):
    """With no env/override/sentinel, values come from policy_defaults.toml --
    the reviewed, versioned statement of production posture (all four ON)."""
    for key in ("queue.dedup_rebuild", "queue.janitor",
                "builder.engine_build", "architect.anchor_refill"):
        v, s = policy.resolve(key, iso["directives"])
        assert v is True and s == "policy_defaults.toml"
    v, s = policy.resolve("architect.max_proposed_depth", iso["directives"])
    assert v == 40 and s == "policy_defaults.toml"


def test_legacy_sentinel_outranks_defaults_and_explicit_zero_means_off(iso):
    sf = iso["directives"] / ".queue_janitor_on"
    sf.write_text("0", encoding="utf-8")   # explicit OFF beats defaults=true
    v, s = policy.resolve("queue.janitor", iso["directives"])
    assert v is False and s == "legacy_sentinel"
    sf.write_text("1", encoding="utf-8")
    assert policy.flag("queue.janitor", iso["directives"]) is True


def test_override_outranks_sentinel(iso):
    (iso["directives"] / ".engine_build_on").write_text("1", encoding="utf-8")
    policy.set_override("builder.engine_build", False)
    v, s = policy.resolve("builder.engine_build", iso["directives"])
    assert v is False and s == "override_file"


def test_env_outranks_everything(iso, monkeypatch):
    policy.set_override("queue.janitor", True)
    (iso["directives"] / ".queue_janitor_on").write_text("1", encoding="utf-8")
    monkeypatch.setenv("ZO_QUEUE_JANITOR", "0")
    v, s = policy.resolve("queue.janitor", iso["directives"])
    assert v is False and s == "env:ZO_QUEUE_JANITOR"


def test_int_and_str_keys_coerce(iso, monkeypatch):
    policy.set_override("architect.max_proposed_depth", "55")
    assert policy.value("architect.max_proposed_depth", iso["directives"]) == 55
    monkeypatch.setenv("DGG_MAX_PROPOSED_DEPTH", "70")
    assert policy.value("architect.max_proposed_depth", iso["directives"]) == 70
    assert "zo-ladder" in policy.value("builder.engine_rungs", iso["directives"])


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

def test_malformed_override_fails_open_to_defaults(iso):
    iso["override"].parent.mkdir(parents=True, exist_ok=True)
    iso["override"].write_text("{not json", encoding="utf-8")
    v, s = policy.resolve("queue.janitor", iso["directives"])
    assert v is True and s == "policy_defaults.toml"


def test_unknown_key_raises_cleanly(iso):
    with pytest.raises(KeyError):
        policy.resolve("nonsense.key", iso["directives"])


def test_set_unset_round_trip_atomic(iso):
    policy.set_override("queue.janitor_limit", 50)
    assert policy.value("queue.janitor_limit", iso["directives"]) == 50
    assert json.loads(iso["override"].read_text(encoding="utf-8")) == {
        "queue.janitor_limit": 50}
    assert policy.clear_override("queue.janitor_limit") is True
    assert policy.value("queue.janitor_limit", iso["directives"]) == 200
    assert not list(iso["override"].parent.glob("*.tmp"))


def test_set_validates_before_persisting(iso):
    with pytest.raises(ValueError):
        policy.set_override("queue.janitor_limit", "not_an_int")
    assert not iso["override"].exists()  # nothing half-written


# ---------------------------------------------------------------------------
# Migration + snapshot
# ---------------------------------------------------------------------------

def test_migrate_folds_sentinels_into_override(iso):
    (iso["directives"] / ".dedup_rebuild_on").write_text("1", encoding="utf-8")
    (iso["directives"] / ".engine_build_on").write_text("0", encoding="utf-8")
    migrated = policy.migrate_legacy(iso["directives"])
    assert migrated == {"queue.dedup_rebuild": True,
                        "builder.engine_build": False}
    # After migration a git clean eating the sentinels changes NOTHING:
    (iso["directives"] / ".dedup_rebuild_on").unlink()
    (iso["directives"] / ".engine_build_on").unlink()
    assert policy.flag("queue.dedup_rebuild", iso["directives"]) is True
    v, s = policy.resolve("builder.engine_build", iso["directives"])
    assert v is False and s == "override_file"


def test_snapshot_covers_every_key_with_provenance(iso):
    snap = policy.snapshot(iso["directives"])
    assert set(snap) == set(policy.KEYS)
    assert all("value" in i and "source" in i for i in snap.values())


# ---------------------------------------------------------------------------
# Consumers resolve through policy
# ---------------------------------------------------------------------------

def test_module_gates_follow_policy_override(iso):
    for mod, key in ((queue_janitor, "queue.janitor"),
                     (engine_build, "builder.engine_build"),
                     (anchor_refill, "architect.anchor_refill")):
        assert mod.enabled(iso["directives"]) is True      # declared posture
        policy.set_override(key, False)
        assert mod.enabled(iso["directives"]) is False     # live flip, no env
        policy.clear_override(key)


def test_janitor_limit_flows_from_policy(iso, tmp_path):
    policy.set_override("queue.janitor_limit", 1)
    home = tmp_path / "home"
    (home / "directives" / "pending").mkdir(parents=True)
    (home / "directives" / "proposed").mkdir(parents=True)
    (home / "lessons").mkdir()
    for i in range(3):
        (home / f"mod_{i}.py").write_text("#" * 64, encoding="utf-8")
        (home / "directives" / "pending" / f"g{i}.json").write_text(
            json.dumps({"task": f"build_mod_{i}", "output_file": f"mod_{i}.py",
                        "handler": "generate_file", "description": "x" * 60}),
            encoding="utf-8")
    stats = queue_janitor.run_pass(home / "directives",
                                   quarantine_dirs=[home / "directives"])
    assert stats["retired"] == 1   # bounded by the policy value, not the 200 default


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
