#!/usr/bin/env python3
"""live_spine_source.py -- $0 live Fly prod /spine/health + /version source
for zo_sentinel/directive_knowledge_sources.py and shadow_corpus/relabel.py.

Closes Dark Edge #4 uncovered in KL-BUILDER-APP-LOOP-01 & KL-DEPLOY-GATE-DECOUPLE-02:
  1. `live_prod_spine_map()` fetches https://mcprisky.io/spine/health + /version
     ($0 public HTTP JSON, zero Fly PG load) and formats a compact prompt section
     showing prod running SHA, mounted count, skipped_no_router, and failures[]
     so sentinel_directive_generator stops building blind to deployed reality.
  2. `spine_mounted_services()` returns the verified live mounted set (or None on
     network/parse error -- never [] on error) for shadow_corpus/relabel.py to
     promote corpus rows from `mounted`/`staged` to `spine_live='live'`.

Usage:
  python live_spine_source.py --print-map
  python live_spine_source.py --self-test
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from typing import Any


def _http_json(url: str, timeout: float = 8.0) -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "zo-sentinel-spine-source/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def spine_mounted_services(
    base_url: str = "https://mcprisky.io",
    fetcher=_http_json,
) -> set[str] | None:
    """Return live mounted service names, or None when /spine/health is unreachable/invalid."""
    doc = fetcher(base_url.rstrip("/") + "/spine/health")
    if not isinstance(doc, dict) or not doc.get("ok"):
        return None
    mounted = doc.get("mounted")
    if not isinstance(mounted, list):
        return None
    return {str(x) for x in mounted if str(x).strip()}


def live_prod_spine_map(
    base_url: str = "https://mcprisky.io",
    fetcher=_http_json,
) -> str:
    """Format a compact <=1.5 KB prompt section for directive_knowledge_sources."""
    ver = fetcher(base_url.rstrip("/") + "/version")
    spine = fetcher(base_url.rstrip("/") + "/spine/health")
    if not ver and not spine:
        return "## Live Prod Spine (mcprisky.io)\n- unavailable (UNKNOWN; do not infer prod state)\n"

    sha = (ver or {}).get("git_sha", "UNKNOWN")
    built_at = (ver or {}).get("built_at", "UNKNOWN")
    lines = [
        "## Live Prod Spine (mcprisky.io -- DEPLOYED TRUTH)",
        f"- prod_git_sha: `{sha[:12] if isinstance(sha, str) else sha}` (built_at={built_at})",
    ]
    if isinstance(spine, dict):
        mounted = spine.get("mounted") or []
        skipped = spine.get("skipped_no_router") or []
        failures = spine.get("failures") or []
        lines.append(
            f"- spine: ok={spine.get('ok')} service_count={spine.get('service_count')} "
            f"mounted={len(mounted) if isinstance(mounted, list) else mounted} "
            f"skipped={len(skipped) if isinstance(skipped, list) else skipped} "
            f"failures={len(failures) if isinstance(failures, list) else failures}"
        )
        if isinstance(skipped, list) and skipped:
            lines.append("- skipped_no_router (existing code missing `router`): " + ", ".join(str(x) for x in skipped[:15]))
        if isinstance(failures, list) and failures:
            lines.append("- failures[] (FIX PRIORITY): " + ", ".join(str(x) for x in failures[:15]))
        if isinstance(mounted, list) and mounted:
            lines.append("- already_mounted_services (DO NOT REBUILD FROM SCRATCH): " + ", ".join(str(x) for x in sorted(mounted)[:40]))
    else:
        lines.append("- spine: unavailable (UNKNOWN)")
    return "\n".join(lines) + "\n"


def run_self_test() -> int:
    # Pole B: unreachable endpoint must report UNKNOWN / None (never silent empty pass)
    s_down = spine_mounted_services(fetcher=lambda _u: None)
    assert s_down is None, s_down
    m_down = live_prod_spine_map(fetcher=lambda _u: None)
    assert "UNKNOWN" in m_down, m_down

    # Pole A: reachable endpoint parses mounted[], skipped[], failures[], and git_sha
    def _ok_fetch(url: str):
        if url.endswith("/version"):
            return {"git_sha": "bf30e09da3d3", "built_at": "2026-11-12T00:56:42Z"}
        return {
            "ok": True,
            "service_count": 3,
            "mounted": ["verdict_summary_api", "ask_answer_api"],
            "skipped_no_router": ["entity_report_exporter"],
            "failures": [],
        }

    s_up = spine_mounted_services(fetcher=_ok_fetch)
    assert s_up == {"verdict_summary_api", "ask_answer_api"}, s_up
    m_up = live_prod_spine_map(fetcher=_ok_fetch)
    assert "bf30e09da3d3" in m_up and "entity_report_exporter" in m_up and "verdict_summary_api" in m_up, m_up
    print("SELF_TEST_PASS: Pole B (UNKNOWN on unreachable) & Pole A (parsed mounted/skipped/failures + git_sha) verified.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Live /spine/health + /version source for directive_knowledge_sources & relabel.")
    ap.add_argument("--print-map", action="store_true", help="Fetch live mcprisky.io and print formatted map.")
    ap.add_argument("--self-test", action="store_true", help="Run hermetic two-pole self-test.")
    args = ap.parse_args(argv)
    if args.self_test:
        return run_self_test()
    if args.print_map:
        print(live_prod_spine_map())
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
