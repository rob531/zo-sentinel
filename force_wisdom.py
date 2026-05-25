#!/usr/bin/env python3
"""Force a wisdom synthesis right now without restarting the daemon."""
import sys
sys.path.insert(0, "/home/workspace/zo_mesh")
sys.path.insert(0, "/home/workspace/zo_sentinel")
from wisdom_synthesiser import synthesise_wisdom, write_sqlite_wisdom, write_wisdom_md
import requests, json
from datetime import datetime, timezone

print("Forcing wisdom synthesis...")
wisdom = synthesise_wisdom()
if wisdom:
    write_sqlite_wisdom(wisdom)
    write_wisdom_md(wisdom)
    print(f"Backend: {wisdom.get('inference_backend')}")
    print(f"Inputs:  {wisdom['inputs_used']}")
    print(f"Wisdom preview:")
    print(wisdom['wisdom'][:400])
else:
    print("No wisdom generated -- check logs")