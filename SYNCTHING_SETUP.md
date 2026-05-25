# Syncthing Pairing — Tower ↔ ZoComputer

**Status check first:** Syncthing may or may not already be installed on ZoComputer. Run the check step before assuming anything.

---

## Step 1 — Check ZoComputer-side state (in ZoComputer terminal)

```bash
which syncthing
ps -ef | grep -i syncthing | grep -v grep
ls -la ~/.config/syncthing/ 2>/dev/null || echo "no config dir"
```

Three possible outcomes:

**A. Syncthing installed and running** — skip to Step 3.  
**B. Syncthing installed but not running** — skip to Step 2b.  
**C. Not installed** — do Step 2a then 2b.

---

## Step 2a — Install Syncthing on ZoComputer

```bash
# Try apt first
sudo apt-get update && sudo apt-get install -y syncthing

# If apt fails or version is old, use the official binary
curl -L -o /tmp/syncthing.tar.gz https://github.com/syncthing/syncthing/releases/latest/download/syncthing-linux-amd64.tar.gz
tar -xzf /tmp/syncthing.tar.gz -C /tmp/
sudo mv /tmp/syncthing-linux-amd64-*/syncthing /usr/local/bin/
syncthing --version
```

## Step 2b — Start Syncthing under supervisord (persistent across reboots)

Watchdog cron does NOT survive ZoComputer reboots — use supervisord per house rules.

Add to `/etc/zo/supervisord-user.conf`:

```ini
[program:syncthing]
command=/usr/local/bin/syncthing -no-browser -home=/home/robin/.config/syncthing
autostart=true
autorestart=true
stdout_logfile=/home/workspace/logs/syncthing.log
stderr_logfile=/home/workspace/logs/syncthing.err
user=robin
```

Then:

```bash
supervisorctl -c /etc/zo/supervisord-user.conf reread
supervisorctl -c /etc/zo/supervisord-user.conf update
supervisorctl -c /etc/zo/supervisord-user.conf status syncthing
```

First start writes default config to `~/.config/syncthing/config.xml`. Wait ~10 seconds, then proceed.

---

## Step 3 — Get ZoComputer's Device ID (CLI, no GUI needed)

```bash
syncthing --device-id
```

Output is a string like `XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX`. **Copy it.** This is the ID the tower needs.

## Step 4 — Get Tower's Device ID

On the tower's Syncthing GUI (http://localhost:8384) → Actions menu → Show ID. **Copy it.**

---

## Step 5 — Network reachability decision

Syncthing uses TCP 22000 (sync) and UDP 21027 (discovery). ZoComputer is in a cloud env and may not have inbound 22000 reachable from the tower's home network. Three options ordered by likelihood-of-just-working:

**5a. Try direct first.** Syncthing has built-in relay servers and discovery. If your tower has typical residential NAT and ZoComputer has any outbound connectivity, the relay path usually works without config. Try Step 6 — if devices show "Connected" within 2-3 minutes, you're done. Skip 5b/5c.

**5b. Cloudflare Tunnel for port 22000.** If 5a doesn't connect, expose ZoComputer's 22000 via the existing Cloudflare account (per HARDWARE/PROVIDER notes you already have a Cloudflare DNS setup).

```bash
cloudflared tunnel route ip add 22000/tcp <tunnel-name>
```

Then on tower side, add the resolved hostname:port as a static address for the ZoComputer device.

**5c. Fall back to ZoComputer-as-introducer-only.** Configure ZoComputer to only accept connections from tower (push model). Tower opens the connection, ZoComputer never needs inbound. This is set per-device in the GUI: “Addresses: dynamic” on tower, ZoComputer has tower in its known device list.

---

## Step 6 — Add devices on both ends

**On tower GUI:** Add Remote Device → paste ZoComputer's Device ID → Save.

**On ZoComputer (CLI, since GUI is harder to reach):** Use the Syncthing REST API.

```bash
APIKEY=$(grep -oP '(?<=<apikey>)[^<]+' ~/.config/syncthing/config.xml)
TOWER_DEVICE_ID="<paste-tower-id-here>"

curl -X POST -H "X-API-Key: $APIKEY" \
  -H "Content-Type: application/json" \
  http://localhost:8384/rest/config/devices \
  -d "{\"deviceID\":\"$TOWER_DEVICE_ID\",\"name\":\"tower-p520\",\"addresses\":[\"dynamic\"]}"
```

Within 1-2 minutes both devices should show as Connected on the tower GUI.

---

## Step 7 — Add the shared folder

**On tower GUI:** Add Folder.  
- Folder Path: `C:\Users\robin\ZoComputer\shared`  
- Folder ID: `zomesh-shared` (must match on both ends)  
- Sharing tab: tick the ZoComputer device  
- Save.

**On ZoComputer CLI:**

```bash
curl -X POST -H "X-API-Key: $APIKEY" \
  -H "Content-Type: application/json" \
  http://localhost:8384/rest/config/folders \
  -d '{"id":"zomesh-shared","label":"ZoMesh Shared","path":"/home/workspace/shared","type":"sendreceive","devices":[{"deviceID":"<TOWER_DEVICE_ID>"}]}'
```

Within 30 seconds both sides should show the folder as syncing. The five subfolders + their READMEs already on ZoComputer will replicate to the tower.

---

## Step 8 — Verify

**Tower:** open `C:\Users\robin\ZoComputer\shared\` in Explorer. Should see 5 README files showing up.

**Sentinel-style verification (paranoid):** write a marker file on each side, confirm it appears on the other within 30 seconds.

```bash
# On ZoComputer:
echo "sync test from zo $(date)" > /home/workspace/shared/sync_test_zo.txt
```

On tower: file appears. Then on tower: create `sync_test_tower.txt`. On ZoComputer: `ls /home/workspace/shared/sync_test_tower.txt` should resolve.

If both round-trip within 60 seconds, Syncthing pairing is done.

---

## Failure modes to watch for

- **Devices show “Discovering” forever:** relay path blocked. Move to 5b (Cloudflare Tunnel).
- **Folder shows “Out of Sync”:** path permissions. Ensure ZoComputer's Syncthing process can write to /home/workspace/shared/ (should be fine since both run as `robin`).
- **Duplicate-conflict files:** last-write-wins behavior, but Syncthing renames conflicts to `*.sync-conflict-*`. Check periodically.
- **High CPU on ZoComputer:** Syncthing scanning. Tune rescan interval up if needed (default 60s is fine).
- **Cookie/session leakage:** confirm `/home/workspace/Datasets/` and `/home/workspace/zo_sentinel/` are NOT in any synced folder. Per layout: `/shared/` only.

---

Created 2026-04-25 during tower onboarding. Update with actual IDs and Cloudflare Tunnel name once paired.