#!/usr/bin/env python3
"""
probe_zo_api.py -- v2, targeted at api.zo.computer/zo/*

Auth: ZO_CLIENT_IDENTITY_TOKEN (NOT ZO_API_KEY).
Known working endpoint per external source: GET /zo/models on api.zo.computer
returns model identifiers available to this account, routable through Zo balance.

This script:
  1. Hits /zo/models first (the high-confidence endpoint).
  2. If that returns 200, tries sibling paths (/zo/balance, /zo/account, /zo/usage,
     /zo/billing, /zo/credits, /zo/me) to map the full surface.
  3. Prints status + redacted body preview per request.
  4. Read-only. No state change. No logging to mesh. No DB writes.

Run on ZoComputer terminal:
    python3 /home/workspace/zo_sentinel/_scratch/probe_zo_api.py
"""

import os
import sys
import urllib.request
import urllib.error

BASE = "https://api.zo.computer"
PRIMARY = "/zo/models"
SIBLINGS = [
    "/zo/balance",
    "/zo/account",
    "/zo/account/balance",
    "/zo/usage",
    "/zo/billing",
    "/zo/credits",
    "/zo/me",
    "/zo/subscription",
]
TIMEOUT_SEC = 5


def try_get(url: str, token: str) -> tuple[int, str]:
    """Return (status_code, body_preview) or (-1, error_string)."""
    req = urllib.request.Request(url, method="GET", headers={
        "Authorization": token,
        "User-Agent": "zo-api-probe/0.2",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            return resp.status, resp.read(2048).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read(512).decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body
    except urllib.error.URLError as e:
        return -1, f"URLError: {e.reason}"
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


def redact(s: str, token: str, n: int = 400) -> str:
    if token and token in s:
        s = s.replace(token, "[TOKEN_REDACTED]")
    if len(s) > n:
        s = s[:n] + f"...[+{len(s)-n} chars]"
    return s.strip().replace("\n", " ")


def main():
    token = os.environ.get("ZO_CLIENT_IDENTITY_TOKEN", "").strip()
    fallback_token = os.environ.get("ZO_API_KEY", "").strip()

    if not token and not fallback_token:
        print("[FATAL] Neither ZO_CLIENT_IDENTITY_TOKEN nor ZO_API_KEY set.")
        sys.exit(1)
    if not token:
        print("[warn] ZO_CLIENT_IDENTITY_TOKEN not set; falling back to ZO_API_KEY")
        token = fallback_token
    else:
        print(f"[info] ZO_CLIENT_IDENTITY_TOKEN present (length={len(token)})")

    print(f"[info] base URL: {BASE}")
    print(f"[info] timeout: {TIMEOUT_SEC}s per request\n")

    # ----- Step 1: confirm /zo/models works -----
    print("=" * 70)
    print(f"STEP 1 -- primary target: {BASE}{PRIMARY}")
    print("=" * 70)
    status, body = try_get(BASE + PRIMARY, token)
    print(f"  status: {status}")
    print(f"  body:   {redact(body, token, n=800)}\n")

    if status != 200:
        print("[stop] /zo/models did not return 200. Nothing further to probe.")
        print("       Possibilities: wrong token, endpoint changed, account not authorised.")
        sys.exit(0)

    # ----- Step 2: sweep sibling paths -----
    print("=" * 70)
    print("STEP 2 -- sibling path sweep (only runs if Step 1 succeeded)")
    print("=" * 70)
    hits = []
    for path in SIBLINGS:
        status, body = try_get(BASE + path, token)
        preview = redact(body, token, n=200)
        mark = "[HIT]" if status == 200 else ("[AUTH]" if status in (401,403) else ("[404]" if status==404 else "[MISC]"))
        print(f"  {mark}  {status:>4}   {path:<25}  {preview}")
        if status == 200:
            hits.append((path, body))

    print()
    print("=" * 70)
    if hits:
        print(f"CONFIRMED additional endpoints ({len(hits)}):")
        for path, body in hits:
            print(f"\n  {path}:")
            print(f"  {redact(body, token, n=600)}")
    else:
        print("No additional endpoints returned 200.")
        print("This likely means /zo/models is the only user-facing endpoint on this path.")
        print("Balance/usage/account data may live under a different path prefix or be UI-only.")


if __name__ == "__main__":
    main()