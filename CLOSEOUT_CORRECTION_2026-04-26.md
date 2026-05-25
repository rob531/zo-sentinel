# Correction to SESSION_CLOSEOUT_2026-04-26_15UTC.md

The earlier closeout repeatedly referenced "Mac Studio M2 Ultra at 192.168.86.25" as a heavy-inference tier. **This is incorrect.** 192.168.86.25 is the LAN IP of the Lenovo P520 tower itself. There is no separate Mac Studio. The wrong description was carried in `inference_router_service.py` v1.6 comments and propagated through Claude's notes; verified by direct probe from the tower (`Get-NetIPAddress` showed 192.168.86.25 on the tower's Ethernet interface).

## Implications

1. **The router as wired cannot work.** TOWER_URL points at `http://192.168.86.25:11434` — a private LAN IP that ZoComputer (Modal cloud) cannot route to. Any `tower`-tier task in the routing table that's been calling `_check_tower()` has been silently failing, falling through to ollama or sonnet (which is also failing). This explains some of the routing pathology.

2. **For the tower to serve as a router tier, we need a tunnel.** Likeliest: Tailscale. Install on both the tower and the ZoComputer container, get a stable WireGuard IP for the tower from the tailnet, update TOWER_URL to that IP, bind Ollama with `OLLAMA_HOST=0.0.0.0`, and firewall port 11434 to tailnet-only. ~10 minutes.

3. **Tower hardware reality re-confirmed.** Single 238 GB SATA disk (KingFast). Robin's *new HD is sitting next to the machine waiting to be installed* — physical task. NVIDIA Quadro K620 still undriven. Plan to add a cheap 2nd-hand NVIDIA card at some point.

4. **LinkedIn Chrome session is live on the tower.** 17 Chrome processes, started at user logon ~02:55 AM 2026-04-26, ~1.4 GB working set. This is the asset the `linkedin_cookie_refresh` handler in `watch_shared.py` was designed to use. With Windows-MCP now working, Claude can drive Chrome on the tower (cookie refresh, posting, scraping) without disturbing the session.

## Future code cleanup

When the v1.7 MiniMax router patch is re-staged, also fix the comments and log strings in `inference_router_service.py` that refer to "Mac Studio M2 Ultra" — replace with "Lenovo P520 tower (Quadro K620, CPU-only inference for now)" or whatever accurate description matches the state at that time.