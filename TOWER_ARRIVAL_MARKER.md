# Tower Arrival Marker — 2026-04-25

**Tower is live.** Robin is in front of the P520.

## State at first contact (Saturday, 2026-04-25, ~mid-day ET)

- Claude Desktop **not yet installed** on tower
- Claude Desktop config JSON (`claude_desktop_config.json`) **not yet created**
- Syncthing **not yet paired**
- ZoComputer-side `/shared/` **not yet created** (this marker is in /home/workspace/zo_sentinel/, not /shared/)
- Local `C:\Users\robin\ZoComputer\` directory exists, currently empty
- ZoComputer MCP (`newzocompconnect`) reachable from this Claude.ai web session — round-trip verified

## Current session context

Robin is reviewing planned actions from TOWER_ARRIVAL_PREP.md before kicking off Phase 1 execution. zo_write_file + zo_read_file round-trip test in progress as of this file's creation.

## Decisions still pending (block JSON creation)

1. MCP filesystem server scope — full /shared/ read-write OR carved (/shared/directives/ write-only + /shared/code/ read-only)
2. Sync mechanism start order — install Syncthing before or after Claude Desktop
3. Whether to capture LinkedIn cookies in this session or defer

This file is a marker, not a directive. Safe to delete once tower setup is past Phase 1 validation.