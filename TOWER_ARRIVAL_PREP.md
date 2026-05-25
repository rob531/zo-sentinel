# Tower Arrival Prep — Ready State for P520

**Date:** 2026-04-20 (LinkedIn session task added 2026-04-22)
**Hardware ETA:** 2026-04-23 (tomorrow)
**Purpose:** Capture all tower-arrival setup decisions and the Selenium-against-builder experiment design, so day-one setup is execution-not-design.

---

## Phase 1 — Basic productivity unlock (4-6 hours after arrival)

### Shared directory layout (agreed)

Tower-side and ZoComputer-side both mount a synced directory at a known path. Proposed layout:

```
/shared/
  directives/     # directive markdown files; either side writes, either side reads
  outputs/        # completed work; tower Claude Desktop writes, ZoComputer reads
  code/           # working files shared between sides; last-write-wins is fine for solo
  conventions/    # copies of builder_conventions.json and similar static files
  logs/           # agent logs mirrored for tower-side inspection
```

**ZoComputer-only (not synced):**
- /home/workspace/Datasets/ (DBs; never leave ZoComputer)
- /home/workspace/zo_sentinel/ (authoritative code; syncs one-way to tower's /shared/code/readonly-mirror/)
- secrets, env vars, process state

**Tower-only (not synced):**
- Model weights (future: GGUF files for local Ollama + fine-tuning)
- Training datasets once built
- Claude Desktop config and local preferences
- **Browser session state (LinkedIn cookies, other logged-in sessions) — see LinkedIn session task below**

### Sync mechanism decision: Syncthing (primary), Cloudflare Tunnel (fallback)

**Why Syncthing default:**
- Peer-to-peer, no third-party server
- Set-and-forget after initial pairing
- Handles disconnects gracefully
- Free, open source, mature (10+ years)
- No cloud bucket as MITM

**Why Cloudflare Tunnel as fallback:**
- If Syncthing struggles with NAT traversal from your home network
- Uses your existing Cloudflare account
- Good for specific point-to-point transfers (e.g. large model downloads)
- Heavier setup but more control

**Do not consider:** Rclone with cloud bucket intermediary. Adds latency, third-party trust surface, and ongoing cost.

### Claude Desktop setup checklist

- [ ] Install Claude Desktop on tower (Windows 11 Pro)
- [ ] Log in with existing Anthropic account (Pro or Max subscription — decide which)
- [ ] Configure MCP filesystem server pointing at /shared/
- [ ] Configure MCP server allowlist: filesystem, github (for read-only repo browsing), optionally puppeteer-for-selenium (see Phase 3)
- [ ] Test a round-trip: write directive on ZoComputer, verify Claude Desktop sees it, write response, verify ZoComputer sees that

### LinkedIn session cookie capture (tower-only, day-one critical)

**Why the tower:** The LinkedIn agent cluster (`t1.linkedin_boost`, `t1.linkedin_post_ideas`, `t1.linkedin_profile_boost`, `t1.linkedin_engagement` — see RETROFIT.md) depends on `LINKEDIN_LI_AT` and `LINKEDIN_JSESSIONID` cookies scraped from an authenticated browser session. Biometric 2FA runs on the Pixel and pairs more reliably with Chrome on native Windows than with anything running inside ZoComputer's environment. Session token caches ~20 days, so this is a recurring task, not one-shot.

**Steps (~10 min):**

1. Install Chrome or Firefox on tower. Chrome recommended; matches the environment these cookies were originally captured from.
2. Navigate to `https://www.linkedin.com`. Log in with Robin's credentials. 2FA prompt will route to Pixel — approve via biometric.
3. Open DevTools (F12) → `Application` tab → `Cookies` → `https://www.linkedin.com`.
4. Copy the exact values of these two cookies:
   - `li_at` → becomes `LINKEDIN_LI_AT`
   - `JSESSIONID` → becomes `LINKEDIN_JSESSIONID` (note: value is typically `"ajax:NNNNNN"`; preserve quotes exactly as shown)
5. Write both values to ZoComputer as secrets via the ZoComputer UI. Do NOT paste them into `/shared/`, any git-tracked file, or any synced path. Browser session state is tower-only for a reason.
6. Verify agents pick up the new credentials: tail `/home/workspace/logs/linkedin_boost.log` (or equivalent) for a successful fetch after the next agent cycle. Expect a posted/rendered response, not an auth error.

**Guard against known failure modes:**
- Logging out of LinkedIn anywhere else (phone app, another browser) can invalidate the session. If any agent starts 401-ing, first hypothesis is cookie invalidation, not code change.
- Cookie values with special chars need proper quoting when entered as secret values. ZoComputer UI handles this; command-line env exports need single-quotes.
- Do not reuse an older `JSESSIONID` across sessions; copy the fresh one each capture.

**Set recurrence:** Calendar reminder for ~18 days after capture to refresh before expiry. Alternative (if schedule tight): add a mesh-side heartbeat check that 401s on LinkedIn agents trigger a tower-bridge trigger file (`/shared/triggers/linkedin_cookie_refresh.request`) for Robin to notice — but only build that later, not day-one.

### Validation criterion (unchanged)

Can Robin write a directive on ZoComputer, walk away, return to find Claude Desktop has done meaningful work without paste-buffering screenshots? If yes, Phase 1 is done and the $377 was well spent.

**Additional validation specific to LinkedIn capture:** after cookie capture, at least one LinkedIn agent produces a fresh successful output (e.g. `t1.linkedin_post_ideas` generates today's ideas) within one scheduled cycle.

---

## Phase 2 — Local inference readiness (post GPU, whenever that is)

### Storage prep (pre-GPU)

- 256GB drive is enough for Phase 1 + planning docs
- When 1TB NVMe becomes sane (< $100), install and move:
  - /models/ for GGUF weights
  - /training/ for fine-tuning corpora
  - /shared/ stays on primary drive or moves to 1TB depending on size

### GPU checklist (when RTX 3060 12GB or better arrives)

- [ ] Verify PSU connectors (P520's 690W is plenty; confirm PCIe 8-pin availability)
- [ ] Install NVIDIA drivers (not game-ready; Studio or Data Center branch)
- [ ] Install CUDA 12.x toolkit
- [ ] Install Ollama for Windows
- [ ] Pull a test model: `ollama pull llama3.2:3b` then `ollama pull phi3:mini`
- [ ] Benchmark tokens/sec on both (expect 30-60 t/s for 3B on 3060)
- [ ] Install llama.cpp (watch for TurboQuant mainline merge per earlier notes)

---

## Phase 3 — Selenium-against-builder experiment

**Goal:** Close the loop where the builder doesn't know what builder has already built. Selenium agent on tower walks the builder UI, produces a structured inventory, feeds back into builder prompts so new feature proposals complement rather than duplicate.

### Why this belongs on the tower, not on ZoComputer

- Selenium is heavyweight (full browser driver, ~500MB RAM, CPU-bound during crawl)
- Running on tower keeps ZoComputer's scarce resources free for the mesh itself
- Cadence is low (once a day is enough); can run overnight
- Output is small structured JSON that syncs back cheap

### Loop design

```
[Tower Selenium agent]
    ↓ crawls builder UI at zo-task-router-robinc.zocomputer.io + any other exposed UIs
    ↓ tree-walks: starts at /, follows every internal link, catalogs forms/buttons/endpoints
    ↓ produces ui_inventory.json:
      {
        "scanned_at": "2026-04-21T08:00:00Z",
        "pages": [
          {"url": "/", "title": "Builder Home", "elements": [...]},
          {"url": "/directives", "title": "Directives Queue", ...}
        ],
        "endpoints": ["POST /api/directives", "GET /api/builds", ...],
        "missing_features_candidates": [...]  # see below
      }
    ↓ syncs to /shared/conventions/ui_inventory.json

[ZoComputer builder]
    ← reads ui_inventory.json at directive-generation time
    ← injects a "Current UI surface:" section into the directive generator's prompt
    ← directive generator can now propose features that complement existing UI vs. duplicate
```

### What counts as a "missing feature candidate"

The Selenium agent doesn't just catalog — it also flags potential gaps. Heuristics:
- A page with a "Retry" button for builds but no "Rerun with different model" option → flag
- Directives list but no filter/sort controls → flag
- An endpoint exposed but no UI surface consuming it → flag
- A stat shown ("9% retry rate") but not clickable to drill down → flag

These aren't guaranteed features, they're hypotheses. The builder's directive generator sees them and decides which are worth pursuing.

### Staging for after tower arrival

- [ ] Confirm what the builder UI looks like (currently running at zo-task-router-robinc.zocomputer.io:3110?). User to confirm URL or paste screenshot once in front of it.
- [ ] Write the Selenium crawler (Python + selenium or playwright; playwright preferred for modern single-page apps). ~200 lines.
- [ ] Write the inventory-to-prompt-section transformer. ~50 lines.
- [ ] Integrate into ZoComputer builder's directive-generation prompt building.
- [ ] Run manually once, review output, tune what gets flagged.
- [ ] Schedule as daily run on tower.

### Risks to think about before building

1. **UI stability.** If builder UI changes often, Selenium selectors break. Use role-based + accessibility-name selectors where possible, not XPath. Fall back to visual screenshot + Claude-vision if needed.

2. **Feedback loop amplification.** If Selenium flags a missing feature and builder builds it, and Selenium re-scans and finds it's now there, and builder's prompt updates — fine. But if the flagging heuristics are noisy, builder might spend effort on low-value proposals. Mitigate: builder treats flagged features as hypotheses requiring explicit review before scheduling, not as auto-build directives.

3. **Authentication.** If the UI requires login, Selenium needs credentials. Store in a tower-side .env, never in /shared/. Consider session-token caching to avoid repeated auth. **Same discipline as LinkedIn session capture — see Phase 1.**

4. **Page coverage gaps.** Single-page apps with heavy JS routing may hide routes from naive crawlers. Use Playwright's route-discovery via intercepted fetch calls to augment link-following.

---

## Phase 4 — Builder memory integration (independent of tower)

Builder-convention injection can ship today against existing ZoComputer infrastructure. Does not require tower. Sequence:

1. Ship builder_conventions.json (DONE — /home/workspace/zo_sentinel/builder_conventions.json)
2. Patch zo_sentinel_builder.py to load and inject conventions into prompt preamble
3. Observe rescue rate over 2 weeks; target drop from 9% to ~5%
4. Add pattern retrieval against mesh_memory.build_artifact interface column (keyword match initially)
5. Add failure → convention feedback loop (parse log nightly, surface new rescue reasons as convention candidates)
6. When sqlite-vec activates at 2K memories, upgrade pattern retrieval to embedding similarity

Once tower has GPU: consider fine-tuning a small code-gen model on the accumulated {directive, rich_prompt, good_code} corpus. The tower is where that fine-tuning happens; the corpus is built on ZoComputer regardless.

---

## Open questions for Robin to decide before tower ships

1. **Claude Desktop subscription tier.** Current Pro plan enough, or step up to Max $100 for heavier directive-loop use? Decide after first two weeks of use.

2. **Windows vs WSL2 for dev tooling on tower.** Windows for Claude Desktop native. WSL2 for Python/Selenium work. Dual-use fine but decide upfront.

3. **MCP filesystem server scope.** Full /shared/ read-write, or carve out /shared/directives/ write-only + /shared/code/ read-only? Tighter scope is safer but friction higher.

4. **Whether to run any mesh agents on tower during ZoComputer outages.** The tower was partly justified as insurance against ZoComputer hibernation/outage. Decide whether to install Bun + run a read-only mesh mirror, or keep tower strictly for directive-loop + experiments.

---

## What's ready to execute day-one

- [x] Hardware purchased
- [x] Shared directory layout decided
- [x] Sync mechanism decided (Syncthing primary)
- [x] Claude Desktop checklist written
- [x] Builder conventions file ready to sync
- [x] Selenium experiment designed (implementation pending tower arrival)
- [x] Post-GPU roadmap documented
- [x] LinkedIn session cookie capture procedure documented (Phase 1) — ENABLES LinkedIn agent cluster
- [ ] Confirm tower ship date (CONFIRMED 2026-04-23)
- [ ] Phase 4 builder-memory patch (can ship before tower arrives)