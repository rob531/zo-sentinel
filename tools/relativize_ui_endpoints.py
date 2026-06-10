#!/usr/bin/env python3
"""relativize_ui_endpoints.py -- the codemod half of piece A.

Rewrites every hardcoded `http://127.0.0.1:PORT` / `localhost:PORT` in the
product HTML to its same-origin prefix (from app_routes.RAW_PORT_PREFIX), so the
page works behind ANY origin instead of only when the browser sits on the host.

    http://127.0.0.1:8772/query   ->   /bus/query
    http://127.0.0.1:8781/write   ->   /svc8781/write

Idempotent (already-relative URLs are untouched). DRY-RUN by default; pass
--apply to write. Ports with no mapping are left alone and reported, so nothing
is silently mangled.

    python tools/relativize_ui_endpoints.py            # preview
    python tools/relativize_ui_endpoints.py --apply    # rewrite in place
"""
import os, re, sys, glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app_routes as R

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPLY = "--apply" in sys.argv
PAT = re.compile(r"https?://(?:127\.0\.0\.1|localhost):(\d+)")

def relativize(text):
    unmapped = set()
    def sub(m):
        port = int(m.group(1))
        prefix = R.RAW_PORT_PREFIX.get(port)
        if prefix is None:
            unmapped.add(port)
            return m.group(0)
        return prefix
    return PAT.sub(sub, text), unmapped

def main():
    files = sorted(glob.glob(os.path.join(ROOT, "*.html")))
    total_changes, changed_files, all_unmapped = 0, 0, set()
    for path in files:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
        new, unmapped = relativize(src)
        n = len(PAT.findall(src)) - len(PAT.findall(new))
        all_unmapped |= unmapped
        if new != src:
            changed_files += 1
            total_changes += n
            print(f"  {'rewrite' if APPLY else 'would rewrite'} {os.path.basename(path):38} {n} url(s)")
            if APPLY:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new)
    print(f"\n{'APPLIED' if APPLY else 'DRY-RUN'}: {total_changes} url(s) across {changed_files} file(s)")
    if all_unmapped:
        print(f"UNMAPPED ports (left untouched -- add to app_routes.RAW_PORT_PREFIX): "
              f"{sorted(all_unmapped)}")
    if not APPLY and changed_files:
        print("re-run with --apply to write.")

if __name__ == "__main__":
    main()
