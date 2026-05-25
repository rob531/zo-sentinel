#!/usr/bin/env python3
"""
zo_balance_probe.py -- focused probe for balance/credits endpoint on api.zo.computer

Known working endpoints (confirmed 2026-04-22):
  GET  /models/available  -> returns model catalog
  POST /zo/ask            -> invokes a model

Auth: Authorization: <ZO_CLIENT_IDENTITY_TOKEN>  (raw, no Bearer prefix)

This probe tries plausible balance-ish paths with the same auth style.
Read-only GETs. 3-second timeout. Redacts token from any echoed body.

Run on ZoComputer terminal:
    python3 /home/workspace/zo_sentinel/_scratch/zo_balance_probe.py
"""

import os
import sys
import urllib.request
import urllib.error
import json

BASE = "https://api.zo.computer"
PATHS = [
    "/balance",
    "/credits",
    "/account",
    "/account/balance",
    "/account/credits",
    "/billing",
    "/billing/balance",
    "/me",
    "/me/balance",
    "/usage",
    "/subscribers/balance",
    "/zo/balance",
    "/zo/credits",
    "/zo/account",
    "/zo/usage",
]
TIMEOUT = 3


def redact(s: str, token: str, n: int = 500) -> str:
    if token and token in s:
        s = s.replace(token, "[TOKEN]")
    if len(s) > n:
        s = s[:n] + f" ...[+{len(s)-n} chars]"
    return s.replace("\n", " ").strip()


def try_get(url: str, token: str):
    req = urllib.request.Request(url, method="GET", headers={
        "Authorization": token,
        "User-Agent": "zo-balance-probe/0.1",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read(2048).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read(256).decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body
    except urllib.error.URLError as e:
        return -1, f"URLError: {e.reason}"
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


def main():
    token = os.environ.get("ZO_CLIENT_IDENTITY_TOKEN", "").strip()
    if not token:
        print("[FATAL] ZO_CLIENT_IDENTITY_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    print(f"base: {BASE}")
    print(f"token: present (length={len(token)})")
    print(f"probing {len(PATHS)} paths, {TIMEOUT}s timeout each\n")

    hits = []
    for path in PATHS:
        status, body = try_get(BASE + path, token)
        preview = redact(body, token, n=300)

        if status == 200:
            mark = "[HIT]"
            hits.append((path, body))
        elif status in (401, 403):
            mark = "[AUTH]"
        elif status == 404:
            mark = "[404]"
        elif status == -1:
            mark = "[ERR]"
        else:
            mark = f"[{status}]"

        # only print non-404 lines to keep output readable
        if status != 404:
            print(f"  {mark:<8} {status:>4}   {path:<24}  {preview}")

    print()
    print("=" * 70)
    if hits:
        print(f"FOUND {len(hits)} endpoint(s) returning 200:")
        for path, body in hits:
            print(f"\n{BASE}{path}")
            # try to pretty-print JSON if it parses
            try:
                data = json.loads(body)
                print(json.dumps(data, indent=2)[:1500])
            except Exception:
                print(redact(body, token, n=1500))
    else:
        print("No balance endpoint found on these paths.")
        print("Next step: check https://zo.computer UI for balance, or inspect")
        print("the network tab when loading the billing page to find the real endpoint.")


if __name__ == "__main__":
    main()