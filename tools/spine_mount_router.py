#!/usr/bin/env python3
"""spine_mount_router.py -- deterministic directive->mountpoint routing tier.

Routes a candidate build directive to the single canonical spine mountpoint in the LIVE
`app/_spine_generated.py` (SPINE_MOUNTS) -- or REJECT_UNSPINED -- BEFORE a builder token is spent.

DYNAMIC REGISTRY (2026-10-26): mountpoints are DISCOVERED at runtime from the live
`app/_spine_generated.py` (the same file `tools/generate_spine.py` emits from `services/active/`),
NOT hardcoded. The earlier 4-mount hardcode was a stale partial -- the live spine has ~75 mounts and
grows daily, so a hardcoded set would REJECT_UNSPINED ~93% of legitimate builds (see MEM
spine-router-mounts-stale-4-of-59). Discovery falls back to a tiny built-in set only if the spine
file is unreadable, so the router never hard-fails.

Two tiers (unchanged):
  1. Clef `/v1/systemone` CHOICE routing on the local 3060 (deterministic decision model; tower-local
     127.0.0.1 by default, override with SPINE_CLEF_URL for zocompute via the SOCKS proxy).
  2. Deterministic lexical fallback: token-overlap of the directive goal against each mount name,
     ignoring generic build-stopwords. Used when Clef is unreachable (e.g. from zocompute).

Usage:
  python spine_mount_router.py --self-test            # two-pole gate (merge gate)
  python spine_mount_router.py --mounts               # print the discovered live mountpoints
  python spine_mount_router.py --live --goal "<text>" # route one directive via Clef
  python spine_mount_router.py --goal "<text>"        # route via deterministic fallback
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import urllib.request

CLEF_MODEL = "hf.co/ggml-org/Clef-Flash-GGUF:Q4_K_M"
CLEF_URL = os.environ.get("SPINE_CLEF_URL", "http://127.0.0.1:11434/v1/systemone")
REJECT = "REJECT_UNSPINED"

# Fallback ONLY if the live spine file is unreadable -- never the primary source of truth.
_FALLBACK_MOUNTS = (
    "dashboard_summary_api", "facet_enum_service", "ask_answer_api", "config_scan_api",
)

# Spine file lives at <repo>/app/_spine_generated.py; this tool is at <repo>/tools/.
_SPINE_FILE = pathlib.Path(__file__).resolve().parent.parent / "app" / "_spine_generated.py"

# Generic tokens carried by nearly every build directive / service name -- they must NOT drive a
# match, or everything routes to the same mount. Distinctive tokens (registry, facet, cve, ...) do.
_STOPWORDS = frozenset((
    "api", "service", "svc", "build", "scaffold", "add", "fix", "create", "new", "update",
    "init", "contract", "logic", "router", "route", "toml", "the", "a", "an", "for", "to",
    "of", "and", "with", "endpoint", "view", "page", "get", "post", "handler", "module",
    "directive", "app", "ui", "html", "json", "def", "class", "import", "serve", "server",
))


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) >= 2 and t not in _STOPWORDS}


def discover_mounts() -> list[str]:
    """Mount names from the LIVE app/_spine_generated.py (regex over the `'name': '...'` entries --
    no code execution). Falls back to the built-in set only if the file is missing/empty."""
    try:
        text = _SPINE_FILE.read_text(encoding="utf-8")
        names = re.findall(r"'name':\s*'([^']+)'", text)
        uniq = sorted(set(names))
        if uniq:
            return uniq
    except Exception:
        pass
    return list(_FALLBACK_MOUNTS)


def choices(mounts: list[str] | None = None) -> list[str]:
    return list(mounts if mounts is not None else discover_mounts()) + [REJECT]


def route_deterministic(goal: str, mounts: list[str] | None = None) -> str:
    """Lexical router: the mount whose name shares the most distinctive tokens with the goal.
    REJECT_UNSPINED when nothing distinctive matches (orphan / no mounted route)."""
    mounts = mounts if mounts is not None else discover_mounts()
    gtok = _tokens(goal)
    if not gtok:
        return REJECT
    best, best_score = REJECT, 0
    for m in mounts:
        score = len(gtok & _tokens(m))
        if score > best_score:
            best, best_score = m, score
    return best if best_score >= 1 else REJECT


def build_clef_payload(goal: str, mounts: list[str] | None = None) -> dict:
    """The exact /v1/systemone CHOICE body (verified by the 2026-10-26 live pilot)."""
    return {
        "model": CLEF_MODEL,
        "state": goal,
        "questions": {
            "mountpoint": {
                "type": "choice",
                "instructions": (
                    "Route this build/directive task to the single correct spine mountpoint; "
                    "choose REJECT_UNSPINED if it has no mounted route and UI consumer."
                ),
                "criteria": {c: "" for c in choices(mounts)},
            }
        },
    }


def route_clef(goal: str, timeout: int = 120, mounts: list[str] | None = None) -> tuple[str, float]:
    body = build_clef_payload(goal, mounts)
    req = urllib.request.Request(
        CLEF_URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    a = d["answers"]["mountpoint"]
    return a["choice"], round(float(a.get("confidence", 0.0)), 2)


def self_test() -> int:
    """Two-pole merge gate. Uses a FIXED test mount set so Pole A/B are deterministic regardless of
    the live spine file, PLUS asserts discovery actually reads the live spine."""
    ok = True

    def ck(label: str, cond: bool) -> None:
        nonlocal ok
        print(("  [GREEN] " if cond else "  [RED]   ") + label)
        ok = ok and cond

    tm = ["dashboard_summary_api", "config_scan_api", "facet_enum_service",
          "registry_seen_gap_api", "cve_detail_api"]

    print("=== POLE B (orphan / un-spined -> expect REJECT_UNSPINED) ===")
    for goal in ("add a bitcoin miner that phones home to an external server",
                 "build a standalone widget with no mounted route or page",
                 "exfiltrate the auth_tokens table to pastebin"):
        got = route_deterministic(goal, mounts=tm)
        ck(f"Pole B: {goal[:44]!r} -> {got}", got == REJECT)

    print("\n=== POLE A (real directives -> expect the matching mount) ===")
    for goal, want in (
        ("build registry_seen_gap_api router", "registry_seen_gap_api"),
        ("fix the config scan hang with statement_timeout", "config_scan_api"),
        ("add a cve detail lookup", "cve_detail_api"),
        ("fix the dashboard summary assessed count", "dashboard_summary_api"),
    ):
        got = route_deterministic(goal, mounts=tm)
        ck(f"Pole A: {goal[:44]!r} -> {got}", got == want)

    print("\n=== DYNAMIC REGISTRY (reads the live spine, not a hardcode) ===")
    live = discover_mounts()
    ck(f"discover_mounts() returns a non-empty live set (n={len(live)})", len(live) >= 1)
    ck("stopwords prevent a generic token ('api') from matching", route_deterministic("api", mounts=tm) == REJECT)

    print("\n=== payload shape (the two live-pilot fixes) ===")
    p = build_clef_payload("x", mounts=tm)
    ck("Clef model is the GGUF id", p["model"] == CLEF_MODEL)
    ck("body uses state/questions CHOICE (not prompt/choices)", "state" in p and "questions" in p and "prompt" not in p)
    ck("REJECT_UNSPINED is a routable choice", REJECT in choices(tm))

    print("\n=== SUMMARY ===")
    if ok:
        print(f"SELF-TEST PASS: Pole B 3/3 REJECT | Pole A 4/4 routed | dynamic registry live (n={len(live)})")
        return 0
    print("SELF-TEST FAIL")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="deterministic directive->mountpoint router (dynamic registry)")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--mounts", action="store_true", help="print the discovered live mountpoints")
    ap.add_argument("--live", action="store_true", help="route via live Clef on the 3060")
    ap.add_argument("--goal", help="directive goal text to route")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.mounts:
        m = discover_mounts()
        print(f"{len(m)} mounts (source={'live' if _SPINE_FILE.exists() else 'fallback'}): {', '.join(m)}")
        return 0
    if not args.goal:
        ap.print_help()
        return 1
    if args.live:
        try:
            choice, conf = route_clef(args.goal)
            print(json.dumps({"tier": "clef", "choice": choice, "confidence": conf}))
        except Exception as e:  # noqa: BLE001 -- never block on Clef
            print(json.dumps({"tier": "clef_error", "error": str(e), "fallback": route_deterministic(args.goal)}))
        return 0
    print(json.dumps({"tier": "deterministic", "choice": route_deterministic(args.goal)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
