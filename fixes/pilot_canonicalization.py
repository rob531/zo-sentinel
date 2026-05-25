#!/usr/bin/env python3
"""
pilot_canonicalization.py  -- v2 (corrected endpoint)

Fix 3 architecture pilot. For each of 15 github-source MCP registry
entries, ask ecosyste.ms packages service: 'give me all packages whose
repository URL is this github URL'.

Correct endpoint (verified from ecosyste.ms docs):
  GET https://packages.ecosyste.ms/api/v1/packages/lookup?repository_url=<url>

Expected response shape: JSON array of package objects, each with:
  - ecosystem (npm, pypi, rubygems, etc)
  - name
  - purl

Previous version of this script used /repositories/lookup on the wrong
service (repos.ecosyste.ms handles repositories; packages.ecosyste.ms
handles packages with a repository_url query param).

If response shape differs from assumptions, the script logs the raw
payload for the first error so we can adjust. CAUTION: ecosyste.ms may
return 404 or empty [] for repositories not indexed -- that's not an
error, it's a SOLO classification.

NO DB MUTATIONS. Read-only. Records results to JSON + mesh_memory.

Run:
  python3 /home/workspace/zo_sentinel/fixes/pilot_canonicalization.py

Run in smoke mode (just first 3 lookups, verify endpoint works):
  python3 /home/workspace/zo_sentinel/fixes/pilot_canonicalization.py --smoke
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

RESULT_FILE = Path("/home/workspace/zo_sentinel/fixes/pilot_canonicalization_results.json")
RAW_DEBUG_FILE = Path("/home/workspace/zo_sentinel/fixes/pilot_canonicalization_raw.json")
ENDPOINT = "https://packages.ecosyste.ms/api/v1/packages/lookup"
TIMEOUT = 15
SLEEP_BETWEEN = 0.5
# Politeness: identify ourselves per ecosyste.ms polite-pool guidance
USER_AGENT = "zo-sentinel/1.0 (+internal canonicalization pilot)"

SMOKE = "--smoke" in sys.argv

SAMPLE = [
    ("cda8ef146a11b8a981bba89c95770ad1", "arxiv-mcp-server",      "https://github.com/blazickjp/arxiv-mcp-server"),
    ("2985330d030039b9b0deacaa7219030b", "browser-tools-mcp",     "https://github.com/AgentDeskAI/browser-tools-mcp"),
    ("b4212d4b4d442e7efacb45d4bce01466", "chrome-devtools-mcp",   "https://github.com/ChromeDevTools/chrome-devtools-mcp"),
    ("a1b2c3d4e5f60014",                 "claude-code-mcp",       "https://github.com/anthropics/claude-code"),
    ("ae0aabd0ee7b34a7b917502cea9107f2", "context7",              "https://github.com/upstash/context7"),
    ("f7410b5f9fe8a531862e9b41049648dd", "csharp-sdk",            "https://github.com/modelcontextprotocol/csharp-sdk"),
    ("104de8c3c221bb1d6a60666d94b04d5d", "exa-mcp-server",        "https://github.com/exa-labs/exa-mcp-server"),
    ("04d66bfa225e9a0dd3aab8123a0ad027", "excel-mcp-server",      "https://github.com/haris-musa/excel-mcp-server"),
    ("5dacf6cf3707eac4b03c3d049bbb1b04", "fast-agent",            "https://github.com/evalstate/fast-agent"),
    ("542965ab7bd0bd5fd08f1b795d72e540", "fastmcp",               "https://github.com/PrefectHQ/fastmcp"),
    ("fa124b6a13f7335ffa5ec9eed8dbc2ab", "firecrawl-mcp-server",  "https://github.com/firecrawl/firecrawl-mcp-server"),
    ("8d49e667cf2061b43c72b154900a56e9", "gemini-cli",            "https://github.com/google-gemini/gemini-cli"),
    ("a1b2c3d4e5f60013",                 "mcp-server-browserbase","https://github.com/browserbase/mcp-server-browserbase"),
    ("b894107a38803c1db33a2f468d722328", "mcp-server-docker",     "https://github.com/ckreiling/mcp-server-docker"),
    ("91f3202d5a83380f0f6725cd9b70126a", "mcp-server-kubernetes", "https://github.com/Flux159/mcp-server-kubernetes"),
]

if SMOKE:
    SAMPLE = SAMPLE[:3]  # smoke: validate endpoint before burning 15 calls

headers = {
    "Accept": "application/json",
    "User-Agent": USER_AGENT,
}

_raw_snapshots = []  # Store first few raw responses for debugging


def lookup(github_url: str) -> dict:
    """Return (status, packages_or_err). Non-raising."""
    try:
        r = requests.get(
            ENDPOINT,
            params={"repository_url": github_url},
            headers=headers,
            timeout=TIMEOUT,
        )
        if r.status_code == 404:
            return {"status": "not_found", "packages": []}
        if r.status_code == 429:
            return {"status": "rate_limited", "packages": []}
        if r.status_code != 200:
            return {"status": f"http_{r.status_code}",
                    "error": r.text[:200],
                    "packages": []}
        payload = r.json()
        # Save first 3 raw responses for shape-verification
        if len(_raw_snapshots) < 3:
            _raw_snapshots.append({"url": github_url, "payload": payload})
        # Expected: array of package dicts. Defensive on dict wrapper too.
        if isinstance(payload, list):
            packages = payload
        elif isinstance(payload, dict):
            # Some ecosyste.ms endpoints wrap in {results: [...]} or {packages: [...]}
            packages = payload.get("results") or payload.get("packages") or []
            if not packages and payload:
                # Single package returned as dict? unusual, wrap it
                if "ecosystem" in payload:
                    packages = [payload]
        else:
            packages = []
        return {"status": "ok", "packages": packages}
    except requests.exceptions.Timeout:
        return {"status": "timeout", "packages": []}
    except Exception as e:
        return {"status": "error", "error": f"{type(e).__name__}: {str(e)[:120]}", "packages": []}


def simplify(packages: list) -> list:
    """Strip to fields we care about for the pilot."""
    out = []
    for p in packages:
        if not isinstance(p, dict):
            continue
        out.append({
            "ecosystem": p.get("ecosystem") or p.get("registry"),
            "name": p.get("name"),
            "purl": p.get("purl"),
            "latest_version": p.get("latest_release_number") or p.get("latest_release"),
            "downloads": p.get("downloads"),
        })
    return out


def classify(cousins: list) -> str:
    """Bucket the match outcome."""
    # Filter cousins to exclude github (we already know the github entry exists)
    non_github = [c for c in cousins if c.get("ecosystem") and c["ecosystem"].lower() not in ("github", "git")]
    if not non_github:
        return "SOLO"
    ecosystems = {c["ecosystem"].lower() for c in non_github}
    if len(ecosystems) == 1:
        if "npm" in ecosystems:        return "NPM_BRIDGE"
        if "pypi" in ecosystems:       return "PYPI_BRIDGE"
        return "OTHER_BRIDGE"
    return "MULTI_BRIDGE"


def main():
    print("=" * 60)
    print("pilot_canonicalization.py v2  (corrected endpoint)")
    print(f"Endpoint: {ENDPOINT}")
    print(f"Mode:     {'SMOKE (3 lookups)' if SMOKE else 'FULL (15 lookups)'}")
    print("=" * 60)

    results = []
    summary = {"SOLO": 0, "NPM_BRIDGE": 0, "PYPI_BRIDGE": 0,
               "OTHER_BRIDGE": 0, "MULTI_BRIDGE": 0,
               "ERROR": 0, "NOT_FOUND": 0, "RATE_LIMITED": 0}

    for i, (server_id, name, url) in enumerate(SAMPLE, 1):
        print(f"\n[{i:2}/{len(SAMPLE)}] {name}")
        print(f"         URL: {url}")
        lk = lookup(url)

        if lk["status"] == "not_found":
            bucket = "NOT_FOUND"
            cousins = []
            print(f"         [not_found] repo not indexed by ecosyste.ms")
        elif lk["status"] == "rate_limited":
            bucket = "RATE_LIMITED"
            cousins = []
            print(f"         [rate_limited] backing off 30s")
            time.sleep(30)
        elif lk["status"] != "ok":
            bucket = "ERROR"
            cousins = []
            err_msg = lk.get('error', '')
            print(f"         [{lk['status']}] {err_msg}")
        else:
            cousins = simplify(lk["packages"])
            bucket = classify(cousins)
            if cousins:
                for c in cousins[:4]:
                    eco = c.get('ecosystem', '?')
                    cname = c.get('name', '?')
                    purl = c.get('purl', 'no-purl')
                    print(f"         + {eco}:{cname}  {purl}")
                if len(cousins) > 4:
                    print(f"         + ...{len(cousins)-4} more")
            else:
                print("         (no non-github packages found)")

        summary[bucket] += 1
        results.append({
            "server_id": server_id,
            "name": name,
            "url": url,
            "bucket": bucket,
            "status": lk["status"],
            "cousins": cousins,
        })
        time.sleep(SLEEP_BETWEEN)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for bucket, count in summary.items():
        if count > 0:
            bar = "#" * count
            print(f"  {bucket:14} {count:2}  {bar}")

    # A "bridge" is any finding of a package in a non-github ecosystem.
    # That's what would enable canonicalization.
    bridges = sum(summary[k] for k in ("NPM_BRIDGE", "PYPI_BRIDGE",
                                        "OTHER_BRIDGE", "MULTI_BRIDGE"))
    errors = summary["ERROR"] + summary["RATE_LIMITED"]
    effective_sample = len(SAMPLE) - errors
    if effective_sample > 0:
        bridge_pct = bridges / effective_sample * 100
    else:
        bridge_pct = 0.0

    print(f"\nBridges:  {bridges}/{effective_sample} effective ({bridge_pct:.0f}%)")
    print(f"Errors:   {errors}")

    if errors > len(SAMPLE) // 3:
        verdict = "INCONCLUSIVE -- too many errors; re-run or check API connectivity"
    elif bridges >= effective_sample * 0.5:
        verdict = "GO -- ecosyste.ms finds cross-registry bridges for majority"
    elif bridges >= effective_sample * 0.25:
        verdict = "CONDITIONAL -- some bridges found; may need secondary enricher"
    else:
        verdict = "PIVOT -- MCP ecosystem is github-primary; canonical=github-url may suffice"
    print(f"Verdict: {verdict}")

    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": ENDPOINT,
        "mode": "smoke" if SMOKE else "full",
        "sample_size": len(SAMPLE),
        "summary": summary,
        "bridges_found": bridges,
        "bridge_pct_effective": round(bridge_pct, 1),
        "verdict": verdict,
        "results": results,
    }, indent=2))

    if _raw_snapshots:
        RAW_DEBUG_FILE.write_text(json.dumps(_raw_snapshots, indent=2))
        print(f"\nFirst {len(_raw_snapshots)} raw responses saved to {RAW_DEBUG_FILE.name}")
        print("  (use to verify response-shape assumptions matched reality)")

    print(f"\nResults: {RESULT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())