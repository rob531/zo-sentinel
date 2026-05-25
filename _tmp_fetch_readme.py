#!/usr/bin/env python3
"""Inspector v2: find the actual '## Reference Servers' heading and dump bullets."""
import re
import requests

r = requests.get(
    "https://raw.githubusercontent.com/modelcontextprotocol/servers/main/README.md",
    headers={"User-Agent": "zo-sentinel/1.0"},
    timeout=15,
)
r.raise_for_status()
md = r.text
print(f"total bytes: {len(md)}")

# Find '## ... Reference Servers' (an H2 heading that contains 'Reference Servers')
m = re.search(r"^##\s+.*Reference Servers\s*$", md, re.MULTILINE | re.IGNORECASE)
print(f"H2 match: {m}")
if m:
    start = m.start()
    # Next H2 (start of a completely different top-level section)
    n = re.search(r"^##\s+", md[m.end():], re.MULTILINE)
    end = m.end() + n.start() if n else len(md)
    section = md[start:end]
    print(f"section length: {len(section)} chars")
    print("=== FIRST 1500 CHARS OF SECTION ===")
    print(section[:1500])
    print("=== ... ===")
    print("=== LAST 1500 CHARS OF SECTION ===")
    print(section[-1500:])

# Separately, locate '### Archived' inside
a = re.search(r"^###\s+Archived\s*$", md, re.MULTILINE | re.IGNORECASE)
print(f"\n### Archived match: {a}")
if a:
    print("=== 1200 CHARS FROM '### Archived' ===")
    print(md[a.start():a.start()+1200])