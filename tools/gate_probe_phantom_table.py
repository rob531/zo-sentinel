#!/usr/bin/env python3
"""TEMPORARY PROBE -- delete with its branch. Do not merge. Do not copy.

Probe (b) of the three-probe arming pattern for #4080. This module names a
table that exists on NO plane. referent-verify's TABLES check must go RED and
the merge must be BLOCKED.

The defect is deliberately the REALISTIC shape, not an obvious one: a
single-character typo of a real bus table. `mcp_server_registry` exists;
`mcp_server_registryy` does not. Every static gate in this repo passes this
file -- it compiles, it lints, the SQL is well-formed, and the name resolves to
nothing. That is the entire class the referent checker exists to catch, and it
is why an obviously-fake table name would have been a weaker probe.

Proving the JOB goes red is not enough. #4089 is the reason: arming the routes
check made the job red and did NOT stop the merge, because GitHub auto-merge
waits only on branch protection's required contexts and `referent-verify` was
not one of them. So this probe checks the MERGE, not the colour.
"""
import requests

WRITE_SERVICE = "http://127.0.0.1:8772"


def probe_query():
    return requests.post(
        f"{WRITE_SERVICE}/query",
        json={"sql": "SELECT server_id, name FROM mcp_server_registryy LIMIT 1"},
        timeout=5,
    )


if __name__ == "__main__":
    raise SystemExit("this is a gate probe; it is not meant to be run")
