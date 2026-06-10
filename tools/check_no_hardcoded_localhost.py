#!/usr/bin/env python3
"""check_no_hardcoded_localhost.py -- the convergence GATE for piece A.

Fails (exit 1) if any product HTML hardcodes `http://127.0.0.1:PORT` or
`localhost:PORT`. Run it in pr-gates so the build loop stops MINTING new UIs
with the localhost pattern -- otherwise the codemod debt regrows every cycle.

    python tools/check_no_hardcoded_localhost.py        # exit 1 if any found

Zero deps. ast-grep equivalent (if you prefer it in the ast-grep ruleset):
    id: no-hardcoded-localhost-in-ui
    language: html
    rule: { regex: 'https?://(127\\.0\\.0\\.1|localhost):[0-9]+' }
"""
import os, re, sys, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAT = re.compile(r"https?://(?:127\.0\.0\.1|localhost):\d+")

def main():
    offenders = []
    for path in sorted(glob.glob(os.path.join(ROOT, "*.html"))):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                for m in PAT.finditer(line):
                    offenders.append((os.path.basename(path), i, m.group(0)))
    if offenders:
        print(f"FAIL: {len(offenders)} hardcoded localhost URL(s) in UI "
              f"(breaks behind any non-localhost origin -- run tools/relativize_ui_endpoints.py --apply):")
        for f, i, u in offenders:
            print(f"  {f}:{i}  {u}")
        return 1
    print("OK: no hardcoded localhost URLs in UI.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
