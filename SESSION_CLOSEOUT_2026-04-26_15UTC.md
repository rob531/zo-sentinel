# Tower Resilience Achieved — 2026-04-26 ~15:30 UTC

Two days of "tower not paying its rent" — ended in this session. Tower is now production-resilient infrastructure with a verified end-to-end recovery loop.

---

## Hardware reality (calibrated)

| Component | What it is |
|---|---|
| **CPU** | Intel Xeon W-2223 (4 cores / 8 threads, 3.6 GHz) |
| **RAM** | 16 GB in 1 of 8 slots, single-channel, expandable to 128+ GB |
| **GPU** | NVIDIA Quadro K620 (2 GB) physically present, **drivers NOT installed** (shows as MS Basic Display Adapter) |
| **Disk** | 173 GB free of 237 GB |

**Inference performance (CPU-only):** phi3:mini @ **15.8 tok/s** on this hardware. Fine for routing/classification; not for heavy gen.

**Tower's role (recalibrated):** automation + control plane + small-model inference + Selenium + file mirror. NOT heavy LLM compute. Mac Studio M2 Ultra at 192.168.86.25 remains the heavy-inference tier.

---

## What landed this session

### Power / OS hardening
- Sleep, hibernate, fast-startup all disabled
- `tower_harden.ps1` is at `C:\Users\robin\ZoComputer\scripts\tower_harden.ps1` (idempotent, re-runnable)

### Tooling installed
- `uv` at `C:\Users\robin\.local\bin\uv.exe` (Python pkg mgr for Claude Desktop MCPs)
- Ollama 0.21.2 daemon up
- `phi3:mini` (~2.2 GB) pulled and verified
- NSSM at `C:\Tools\nssm\nssm.exe` (Windows service wrapper)

### Syncthing now a true Windows Service
- Installed via NSSM as `Syncthing` service
- StartMode=Auto, ObjectName=LocalSystem
- **Survives reboot without anyone logging in** (the resilience win)
- Logs: `C:\Users\robin\ZoComputer\state\syncthing_service.log` (rotates at 10 MB)
- Old `Syncthing-Tower` scheduled task disabled (kept registered as fallback)

### ZoTowerWatch scheduled task installed
- Fires every 60 min + at logon
- Drops `post_reboot_*.trigger` into `shared\triggers\` if zm-go output is stale (>12h)
- **End-to-end verified:** Tower wrote trigger 15:12:23 → Syncthing carried it → ZoComputer's `watch_shared.py` picked it up 15:13:19 (56s) → ran post_reboot recovery (zm go + zm check)
- Mesh/builder/sentinel stayed healthy throughout the test
- Bonus: `write_service` heartbeat thread came back via the recovery (was 12h stale before)

### Multiple-pathway bridge architecture banked
Any one bridge failure has three alternatives:
1. web claude.ai → `newzocompconnect` MCP → ZoComputer file/db/log
2. web → Filesystem MCP → tower `C:\Users\robin\ZoComputer\` scope
3. web → Windows-MCP → tower PowerShell/Registry/Shortcut full control
4. Claude Desktop on tower (admin) → ZoComputer device skill with write-to-terminal

---

## Resilience profile (current state)

| Failure mode | Recovery |
|---|---|
| ZoComputer nightly reboot, Robin logged in | Auto: ZoTowerWatch fires hourly + on logon |
| ZoComputer crashes during day, Robin logged in | Auto: same path |
| Tower reboots (e.g. Windows Update), Robin logged in | Auto: Syncthing service starts at boot; tasks fire on logon |
| Tower reboots, Robin NOT logged in | **Partial:** Syncthing comes up (now a service); ZoTowerWatch waits for next logon. Triggers queue but don't fire until logon. |
| Power outage, both reboot, Robin away | **Partial gap:** see above. Acceptable for a home tower; Robin's typically present daily. |
| Syncthing crashes | Auto: NSSM restart on failure (default policy) |
| Tower's MCP bridge fails | 3 alternative pathways still work |

---

## Known remaining gaps (low priority)

1. **ZoTowerWatch still requires logon.** Same gap Syncthing had until tonight; could be fixed similarly (change task LogonType to ServiceAccount, or run via SYSTEM context with AtStartup trigger). Not done because A) Robin is typically present, and B) tonight's Syncthing fix already gets us 80% of the value.
2. **No NVIDIA drivers on the K620.** Doesn't matter much; 2 GB VRAM only fits very small models, and CPU inference of phi3:mini is fast enough at 15.8 tok/s.
3. **RAM single-channel.** 16 GB in 1 of 8 slots; doubling channel would meaningfully speed CPU inference. Hardware investment, not tonight.
4. **Modern Standby supported on this BIOS.** Could in theory suspend background processes during "awake" periods; if observed, BIOS-level fix.
5. **Mesh inference router still on v1.6 (Anthropic-only escalation, dead since 04-07).** v1.7 MiniMax patch staged file gone. Re-stage AFTER `inference_router_service.py` gets wrapped in daemon_wrapper.sh — the architectural debt that turned last night's small mistake into a long outage.

---

## Files of record

```
C:\Users\robin\ZoComputer\scripts\tower_harden.ps1                  # power + tooling lockdown (re-runnable)
C:\Users\robin\ZoComputer\shared\code\tower\zo_tower_watch.ps1      # main trigger writer
C:\Users\robin\ZoComputer\shared\code\tower\Install-ZoTowerWatch.ps1 # task installer (re-runnable)
C:\Users\robin\ZoComputer\shared\code\tower\Trigger-*.cmd           # 3 manual triggers (double-click)
C:\Users\robin\ZoComputer\state\zo_tower_watch.log                  # task log (append-only)
C:\Users\robin\ZoComputer\state\syncthing_service.log               # NSSM-managed Syncthing log
C:\Tools\nssm\nssm.exe                                              # NSSM binary
```

---

## Next-priority work (tomorrow or next session)

1. **Wrap `inference_router_service.py` in `daemon_wrapper.sh`** — prerequisite to safely retrying the v1.7 MiniMax patch. — ZoComputer side, not tower.
2. **Re-stage v1.7 MiniMax patch** behind the wrapper. Restores escalation tier (Anthropic dead since 04-07).
3. **Restore the 9 dead trust pipeline services** (still down 5+ days). Unrelated to tower work; same wrap-or-supervisord question.
4. **Investigate world agent memory staleness** (15+ days now). The world agent process is up but memory writes are failing.
5. **Builder velocity probe with phi3:mini as router tier** — add tower endpoint to `inference_router_service.py` routing for `classify`/`tag`/`filter`/`relevance` tasks. Frees ZoComputer Ollama for queue space.

---

*The tower paid its rent today.*