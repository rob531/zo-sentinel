"""staging_drain -- the chain that leaves no staged service without a reason.

Chairman direction (Robin, 2026-10-03): push out everything in staging through the
promotion gate; repair everything else. Exit condition: for every directory under
services/staged exactly ONE of {promoted, superseded, repair directive, retired}
is true. "Staged with no reason" is the defect this package exists to end.

Segments, each a deterministic CLI the tower's spool driver (`_chains/staging_drain/`)
or a human runs in order; each writes a file the next one reads:

  census.py      S1  run the REAL gate (tools/promote_staged_to_active.evaluate) on
                     every staged dir -> census.json (family, version, source, toml,
                     verdict, first failure, import footprint, runtime class)
  dedupe.py      S3  per family keep the newest passing version (or the newest as the
                     repair target); the rest are superseded -> ledger.json
  retire.py      S6  no source => retired with a recorded reason -> ledger.json
  repair.py      S5  mechanical classes fixed by the existing scripts; everything
                     else => one builder directive per repair target, the gate
                     failure quoted verbatim, acceptance = "passes the gate"
  exclusions.py  S7  promoted + superseded families -> the builder's exclusion input
  report.py          the one daily line for the chairman one-pager
  chain_tick.py      runs S1..S7 in order (S4 excluded: promotion needs prod evidence)

Nothing here deletes a directory, bypasses a gate, imports duckdb, or writes
outside the repo tree it is given. Promotion (S4) is `promote_staged_to_active.py
--enforce --only` plus the boot test and a prod-drift check on the tower; a service
is "promoted" in the ledger only when live prod evidence is recorded for it.
"""
