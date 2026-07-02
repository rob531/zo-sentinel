"""
policy.py -- THE declarative operational policy layer for zo-sentinel.

WHY THIS EXISTS (chairman directive 2026-07-02: stop patching, fix the
fundamental): operational behavior had accreted into four ad-hoc gates --
.dedup_rebuild_on, .queue_janitor_on, .engine_build_on, .anchor_refill_on --
each an UNTRACKED sentinel file in directives/ (git-clean-fragile; a daemon
respawn can silently delete them) plus its own env var, each read by its own
slightly-different parser, plus scattered numeric tunables
(DGG_MAX_PROPOSED_DEPTH, ZO_ANCHOR_THRESHOLD, ZO_ENGINE_RUNGS, ...). Nobody
could see the composed posture in one place, and every new feature repeated
the pattern. This module replaces the PATTERN, not just the four instances.

ONE PRECEDENCE CHAIN (lowest to highest), resolved fresh on every read so
flips are live without restarts:

  1. EMBEDDED defaults        -- in this file; the code's own safe posture.
  2. policy_defaults.toml     -- TRACKED in the repo next to this file: the
                                 declarative, reviewed, versioned statement of
                                 intended production posture. Changing it goes
                                 through a PR like any other code.
  3. legacy sentinel files    -- directives/.<name>_on, HONORED for backward
                                 compatibility (exists+truthy = on,
                                 exists+"0" = explicitly off, absent = no
                                 opinion). Deprecated: `migrate` folds them
                                 into the override file.
  4. durable override file    -- JSON at /home/workspace/zo_sentinel_state/
                                 policy_override.json (env
                                 ZO_POLICY_OVERRIDE_PATH). OUTSIDE the git
                                 tree: survives `git clean`, daemon respawns
                                 and refresh_code. This is where live
                                 operational flips go (via the CLI).
  5. environment variables    -- the legacy per-key env names, kept as the
                                 emergency big-hammer (highest precedence).

CLI (run from the repo root):
  python3 -m zo_sentinel.policy show            # full posture + provenance
  python3 -m zo_sentinel.policy get queue.janitor
  python3 -m zo_sentinel.policy set queue.janitor false   # live, no restart
  python3 -m zo_sentinel.policy unset queue.janitor
  python3 -m zo_sentinel.policy migrate         # legacy sentinels -> override

NON-FRAGILE BY CONSTRUCTION: stdlib-only; no import-time side effects; every
file read is best-effort (malformed/missing layers are skipped, never fatal);
consumers call flag()/value() inside try/except with their old inline logic as
the fallback, so a policy-layer fault degrades to yesterday's behavior, never
to a crash. Atomic override writes (tmp + os.replace).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:  # py3.11+; guarded so a policy import can never crash a consumer
    import tomllib
except Exception:  # pragma: no cover
    tomllib = None

_PKG_DIR = Path(__file__).resolve().parent
DEFAULTS_TOML = _PKG_DIR / "policy_defaults.toml"
DEFAULT_OVERRIDE_PATH = "/home/workspace/zo_sentinel_state/policy_override.json"
DEFAULT_DIRECTIVES_ROOT = "/home/workspace/zo_sentinel/directives"

# ---------------------------------------------------------------------------
# The key registry: every operational knob, its type, its embedded default,
# and its LEGACY surfaces (env var + sentinel file) so nothing breaks during
# the transition. Adding a knob = one line here + one line in the TOML.
# ---------------------------------------------------------------------------
KEYS: Dict[str, Dict[str, Any]] = {
    # queue lifecycle
    "queue.dedup_rebuild":        {"type": "bool", "default": False,
                                   "env": "ZO_DEDUP_REBUILD",
                                   "sentinel": ".dedup_rebuild_on",
                                   "doc": "skip redundant rebuilds (goose_runner dedup)"},
    "queue.janitor":              {"type": "bool", "default": False,
                                   "env": "ZO_QUEUE_JANITOR",
                                   "sentinel": ".queue_janitor_on",
                                   "doc": "skip=>retire janitor in the promoter cycle"},
    "queue.janitor_limit":        {"type": "int", "default": 200,
                                   "env": "ZO_JANITOR_LIMIT",
                                   "doc": "max retirements per janitor pass"},
    # builder
    "builder.engine_build":       {"type": "bool", "default": False,
                                   "env": "ZO_ENGINE_BUILD",
                                   "sentinel": ".engine_build_on",
                                   "doc": "grounded deterministic engine fallback"},
    "builder.engine_rungs":       {"type": "str", "default": "zo-ladder-nvidia,zo-ladder-high",
                                   "env": "ZO_ENGINE_RUNGS",
                                   "doc": "csv capable-rung escalation for the engine"},
    "builder.engine_max_repairs": {"type": "int", "default": 1,
                                   "env": "ZO_ENGINE_MAX_REPAIRS",
                                   "doc": "bounded repair rounds per engine build"},
    # architect
    "architect.anchor_refill":    {"type": "bool", "default": False,
                                   "env": "ZO_ANCHOR_REFILL",
                                   "sentinel": ".anchor_refill_on",
                                   "doc": "self-refilling anchor (KL candidate mining)"},
    "architect.refill_threshold": {"type": "int", "default": 5,
                                   "env": "ZO_ANCHOR_THRESHOLD",
                                   "doc": "refill when missing candidates < this"},
    "architect.refill_max_new":   {"type": "int", "default": 5,
                                   "env": "ZO_ANCHOR_MAX_NEW",
                                   "doc": "max candidates appended per refill"},
    "architect.max_proposed_depth": {"type": "int", "default": 40,
                                   "env": "DGG_MAX_PROPOSED_DEPTH",
                                   "doc": "proposed/ depth cap gating generation"},
}

_FALSEY = ("", "0", "off", "false", "no")


def _parse_bool(raw: str) -> bool:
    return str(raw).strip().lower() not in _FALSEY


def _coerce(key: str, raw: Any) -> Any:
    t = KEYS[key]["type"]
    if t == "bool":
        return raw if isinstance(raw, bool) else _parse_bool(str(raw))
    if t == "int":
        return int(raw)
    return str(raw)


def _override_path() -> Path:
    return Path(os.environ.get("ZO_POLICY_OVERRIDE_PATH", DEFAULT_OVERRIDE_PATH))


def _load_defaults_file() -> Dict[str, Any]:
    """Flatten policy_defaults.toml into dotted keys. Best-effort."""
    if tomllib is None or not DEFAULTS_TOML.is_file():
        return {}
    try:
        with DEFAULTS_TOML.open("rb") as f:
            data = tomllib.load(f)
        flat: Dict[str, Any] = {}
        for section, body in data.items():
            if isinstance(body, dict):
                for k, v in body.items():
                    flat[f"{section}.{k}"] = v
            else:
                flat[section] = body
        return flat
    except Exception:
        return {}


def _load_override() -> Dict[str, Any]:
    p = _override_path()
    try:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _sentinel_state(key: str, directives_root: Optional[Path]) -> Optional[bool]:
    """Legacy sentinel semantics: absent = None (no opinion); present =
    truthy-content bool (so an explicit '0' is an explicit OFF)."""
    name = KEYS[key].get("sentinel")
    if not name:
        return None
    root = Path(directives_root) if directives_root else Path(DEFAULT_DIRECTIVES_ROOT)
    try:
        sf = root / name
        if sf.is_file():
            return _parse_bool(sf.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def resolve(key: str, directives_root=None) -> Tuple[Any, str]:
    """(value, source) for a key, applying the full precedence chain.
    Reads fresh every call -- flips are live. Never raises for known keys."""
    if key not in KEYS:
        raise KeyError(f"unknown policy key: {key}")
    value: Any = KEYS[key]["default"]
    source = "embedded_default"

    fd = _load_defaults_file()
    if key in fd:
        try:
            value, source = _coerce(key, fd[key]), "policy_defaults.toml"
        except Exception:
            pass

    st = _sentinel_state(key, directives_root)
    if st is not None:
        value, source = st, "legacy_sentinel"

    ov = _load_override()
    if key in ov:
        try:
            value, source = _coerce(key, ov[key]), "override_file"
        except Exception:
            pass

    env_name = KEYS[key].get("env")
    if env_name:
        raw = os.environ.get(env_name)
        if raw is not None and raw != "":
            try:
                value, source = _coerce(key, raw), f"env:{env_name}"
            except Exception:
                pass
    return value, source


def flag(key: str, directives_root=None) -> bool:
    v, _ = resolve(key, directives_root)
    return bool(v)


def value(key: str, directives_root=None) -> Any:
    v, _ = resolve(key, directives_root)
    return v


def snapshot(directives_root=None) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for key in sorted(KEYS):
        v, s = resolve(key, directives_root)
        out[key] = {"value": v, "source": s, "doc": KEYS[key].get("doc", "")}
    return out


# ---------------------------------------------------------------------------
# Override mutation (the live-ops path) -- atomic, validated, reversible
# ---------------------------------------------------------------------------

def _write_override(data: Dict[str, Any]) -> None:
    p = _override_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, str(p))


def set_override(key: str, raw: Any) -> Any:
    if key not in KEYS:
        raise KeyError(f"unknown policy key: {key}")
    val = _coerce(key, raw)          # validate BEFORE persisting
    data = _load_override()
    data[key] = val
    _write_override(data)
    return val


def clear_override(key: str) -> bool:
    data = _load_override()
    if key in data:
        del data[key]
        _write_override(data)
        return True
    return False


def migrate_legacy(directives_root=None) -> Dict[str, Any]:
    """Fold every present legacy sentinel's state into the durable override
    file (idempotent; sentinels are LEFT IN PLACE as a belt until removed
    deliberately -- but the override now outranks them, so a git clean eating
    a sentinel no longer changes behavior)."""
    migrated: Dict[str, Any] = {}
    for key in KEYS:
        st = _sentinel_state(key, directives_root)
        if st is not None:
            set_override(key, st)
            migrated[key] = st
    return migrated


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli(argv) -> int:
    args = list(argv)
    cmd = args[0] if args else "show"
    root = os.environ.get("ZO_POLICY_DIRECTIVES_ROOT") or None
    if cmd == "show":
        snap = snapshot(root)
        width = max(len(k) for k in snap)
        for k, info in snap.items():
            print(f"{k:<{width}}  = {str(info['value']):<34} [{info['source']}]  # {info['doc']}")
        print(f"\noverride file: {_override_path()}")
        return 0
    if cmd == "get" and len(args) == 2:
        v, s = resolve(args[1], root)
        print(f"{v}  [{s}]")
        return 0
    if cmd == "set" and len(args) == 3:
        v = set_override(args[1], args[2])
        print(f"set {args[1]} = {v} (override file: {_override_path()})")
        return 0
    if cmd == "unset" and len(args) == 2:
        print("removed" if clear_override(args[1]) else "not set")
        return 0
    if cmd == "migrate":
        m = migrate_legacy(root)
        print(f"migrated {len(m)} legacy sentinel(s) into {_override_path()}:")
        for k, v in m.items():
            print(f"  {k} = {v}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
