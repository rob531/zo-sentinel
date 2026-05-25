# Session Closeout — 2026-04-26 ~02:30 UTC

Long, hard session. Closing out in a degraded state.

---

## What's broken right now

1. **Mesh is half-dead.** `supervisorctl restart zo-mesh` earlier in the night took down everything in the mesh_guardian process group (`killasgroup=true`). These are NOT currently running:
   - `write_service.py`
   - `inference_router_service.py`
   - `pipeline_bridge.py`
   - `zo_sentinel_builder.py`
   - `sentinel_directive_generator.py`
   - `watch_shared.py`
2. **Syncthing is dead.** Crashed at ~01:19 UTC; supervisord can't restart it because `[program:syncthing]` was wiped from `/etc/zo/supervisord-user.conf` when ZoComputer regenerated that file from a base image. Last conf size and 1970 mtime confirm a fresh-from-template state.
3. **v1.7 inference_router (MiniMax tier) is rolled back** to v1.6 — the apply script's failure auto-rolled back cleanly. Staging file `inference_router_service.py.new` was consumed by the rename; not present anymore.

## What's NOT broken (worth banking)

1. **Tower-side Claude Desktop + Filesystem MCP + connector inheritance still work** — none of tonight's chaos touched the tower.
2. **Syncthing v2.0.16 is paired** (device IDs intact, config.xml intact at `/home/robin/.config/syncthing/config.xml`). Just needs a process running.
3. **Tower stub scripts are written and on ZoComputer disk** at `/home/workspace/shared/code/tower/` (six files: `zo_tower_watch.ps1`, `Install-ZoTowerWatch.ps1`, three `Trigger-*.cmd`, `README.md`). They will sync to the tower as soon as Syncthing is back up.
4. **`watch_shared.py v1.1` trigger framework on ZoComputer side is intact** — five handlers including `post_reboot`, `zm_go`, `zm_check`. Once watch_shared is running again, the tower stubs work end-to-end.
5. **`zm go` (=`bash /home/workspace/zo_mesh/go.sh`) is the canonical recovery** — restores write_service, inference_router, builder, directive_gen, watch_shared, etc.

## First three actions next session, in order

1. **`zm go`** from a ZoComputer shell. Brings the mesh back. Verify with `zo_agent_health` afterwards — should see write_service, inference_router, builder, directive_generator, pipeline_bridge, watch_shared all in the process list with fresh PIDs.
2. **Restart Syncthing v2 directly** via `python3 /home/workspace/logs/_locate_and_start_syncthing_v2.py` (already written tonight). It will find the v2 binary, launch with v2 syntax (`serve --no-browser --home=...`), wait for the API, and verify the tower link. Once Syncthing is up, the six tower stub files propagate to `C:\Users\robin\ZoComputer\shared\code\tower\` automatically.
3. **Once tower stubs are visible on the tower:** run `Install-ZoTowerWatch.ps1` from elevated PowerShell on the tower. The pre-install `-Test` will validate the environment. After install, tomorrow night's reboot self-heals.

## Architectural debt surfaced tonight (queue, don't fix yet)

1. **`/etc/zo/supervisord-user.conf` is externally managed by ZoComputer.** Hand-edits to add programs survive until the next regen, then vanish. Anything we want auto-restarted must use the `daemon_wrapper.sh` convention (write_service, builder, directive_gen all already do this) or be added to the ZoComputer-managed conf via whatever sanctioned mechanism exists (worth asking ZoComputer support).
2. **Syncthing has no wrapper.** It was the only mesh-adjacent service relying on supervisord directly. Should be wrapped in `syncthing_wrapper.sh` matching the other mesh services, then started by `zm go` step N.
3. **`inference_router_service.py` has no wrapper.** Same gap as Syncthing — exposed tonight when the apply script killed it and nothing brought it back. Wrap before retrying any future patch.
4. **MiniMax tier in mesh router still pending.** Anthropic BYOK still dead since 04-07. Plan unchanged: re-stage v1.7 patch AFTER inference_router is wrapped, so a failed restart doesn't take it down for an hour.
5. **9 trust services still dead since 04-20.** Unchanged. Add to supervisord with `autorestart=true` (acknowledging point 1 above — they may need wrapper-respawn if supervisord-user.conf reset wipes them too).

## Files written tonight (for reference)

```
/home/workspace/logs/_apply_router_minimax_patch.py        (failed; do not re-run as-is)
/home/workspace/logs/_recover_inference_router.py          (restart-zo-mesh, made things worse)
/home/workspace/logs/_start_inference_router_now.py        (orphan-launch fallback)
/home/workspace/logs/_panic_recovery_mesh.py               (multi-service revival; superseded by `zm go`)
/home/workspace/logs/_diagnose_syncthing.py                (read-only diagnostic)
/home/workspace/logs/_sync_capture_and_restart.py          (failed: program block missing from conf)
/home/workspace/logs/_recon_supervisord_for_syncthing.py   (revealed conf got reset)
/home/workspace/logs/_locate_and_start_syncthing_v2.py     (recovery — run next session)
/home/workspace/shared/code/tower/                         (six tower stub files; awaiting sync)
```

## State of the v1.7 patch

Not in the codebase anywhere — staging file was consumed by the (failed) atomic rename. If we want to retry next session, I can recreate it from the design we agreed on; the pattern is fully documented in this conversation. Don't retry until inference_router is wrapped (point 3 above).

---

*Sleep well. Tomorrow's first move is `zm go`, then the locate-and-start syncthing script. Both are one command each.*