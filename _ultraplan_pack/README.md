# THROWAWAY BRANCH â€” DO NOT MERGE

This branch exists for one reason: the Ultraplan cloud container can only see a
GitHub repo, and the planning pack lives on the tower at
`D:\zo\Zocomputer Agents\_ultraplan\`, which is not a git repo at all.

Nothing here is product code. Nothing here should ever reach `main`. If you are
reading this in a PR, the PR is a mistake â€” close it, do not merge it.

## What is here

- `RUN1_LEDGER_RECKONING.md` â€” the entry prompt for run 1. Start here.
- `RUN2_REPO_AUDIT.md` â€” entry prompt for run 2; consumes run 1's output.
- `PACK_README.md` â€” what the pack is, what building it found, how to rebuild.
- `build_pack.py` â€” the builder. Every number in the pack came from this; read
  it if you want to know how a figure was derived rather than trusting it.
- `00_MANIFEST.json` â€¦ `31_ledger_stats.json` â€” the pack itself.

## Read the manifest first

`00_MANIFEST.json` carries `built_at`. If that is more than 24h before the run,
the counts describe a system that has already moved â€” roughly three ledger
entries a day, and eighteen lanes writing nightly. Rebuild rather than proceed.
