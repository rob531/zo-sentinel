# Hardware Purchase Record — 2026-04-20

## What was bought

**Lenovo ThinkStation P520 Tower** — Grade C cosmetic, fully functional
- SKU: 163510
- Source: PCLiquidations.com
- Price: $361.99 + $14.99 (Win 11 Pro upgrade) = **$376.98**
- 30-day returns, 1-year warranty
- Microsoft Authorized Refurbisher (legitimate Windows license)

## Spec as shipped

- CPU: Intel Xeon W-2223 (4-core, 3.6 GHz / 3.9 GHz boost)
- RAM: 16GB DDR4 (8 DIMM slots, expands to 512GB ECC RDIMM)
- Storage: 256GB NVMe SSD (boot/OS)
- PSU: 690W (standard P520 Tower, shared family with P720/P920)
- Slots: 2× PCIe x16 Gen 3, 1× x8, 2× x4, 1× PCI
- Graphics: Intel onboard (no discrete GPU yet)
- OS: Windows 11 Pro
- Form factor: Full Tower

## What was NOT bought today, and why

**Storage upgrade (1TB+ NVMe)** — deferred
- 2025-2026 NAND/DRAM crisis: prices up ~246% from start of 2025
- 1TB NVMe at reputable sellers running $120-180 (vs $55-80 historic)
- New fab capacity not arriving until late 2027/2028 per TrendForce/Phison
- Plan: hold off until prices stabilize OR a sale surfaces; 256GB is enough for OS + Claude Desktop + sync directory

**RTX 3060 12GB GPU** — deferred  
- GPU prices also elevated due to AI demand
- Plan: hold off until either (a) prices come down or (b) specific workload demands it (signal fine-tuning, ASI-Evolve, local inference, TurboQuant experimentation)
- The chassis (PSU 690W, x16 slot free) is ready to accept one whenever

## What this hardware enables on day one (no upgrades needed)

**Primary value: directive-loop extension via Claude Desktop**
1. Claude Desktop installed locally on tower
2. MCP filesystem server points at a shared directory
3. Sync mechanism (Syncthing recommended, or Cloudflare Tunnel + rsync) bridges tower to ZoComputer
4. Robin steps out of "USB cable between Claude and container" role
5. Sunday-style 6-hour iteration sessions compress to ~90 min

**Secondary: insurance against ZoComputer hibernation**
- Tower is always-on (no hibernation in your home)
- If ZoComputer outage, key pieces of mesh can run locally

**Deferred until storage + GPU are added:**
- Local LLM inference at meaningful scale
- Signal fine-tuning for tool_description_safety + permission_scope
- ASI-Evolve experimentation
- TurboQuant KV-cache compression experiments (see future-state note below)
- Anything requiring multi-GB model weights

## Pre-arrival prep checklist

- [ ] Decide shared directory layout (suggested: /shared/directives, /shared/outputs, /shared/code)
- [ ] Pick sync mechanism (Syncthing default, Cloudflare Tunnel alternative)
- [ ] Read Claude Desktop MCP filesystem server docs ahead of time
- [ ] Identify ZoComputer-side mount point for the synced directory
- [ ] Plan Cloudflare Tunnel auth (existing Cloudflare account)
- [ ] Decide whether to migrate any existing tools (likely none day one)

## Post-arrival setup sequence (estimated 4-6 hours)

1. Boot, run Windows updates, install drivers (1-2h)
2. Install Claude Desktop, log in with Anthropic account (15 min)
3. Set up Syncthing or Cloudflare Tunnel (1-2h)
4. Configure MCP filesystem server pointing at shared directory (30 min)
5. Test the loop: write a directive on ZoComputer side, verify Claude Desktop sees it, write a response, verify ZoComputer sees that (30 min)
6. Document what worked and what needed adjustment (30 min)

**Validation criterion for the purchase:** can Robin write a directive on ZoComputer, walk away, come back later and find Claude Desktop has done meaningful work on it without Robin needing to paste screenshots back and forth? If yes, the $377 was well spent regardless of GPU/storage future.

## Future state (when prices allow)

Add in this order:
1. **1TB NVMe** when price drops below $100 — enables larger model storage and dataset work
2. **RTX 3060 12GB** when price stabilizes — enables local inference, small-model fine-tuning, and the experimentation items below
3. **Additional 32GB DDR4 ECC RDIMM** if RAM becomes bottleneck — brings to 48GB total

None of these are urgent. The directive-loop value lands on day one without them.

## Future experimentation items (post-GPU)

### TurboQuant (Google Research, ICLR 2026)

**What it actually is:** A KV-cache quantization algorithm. Compresses the key-value cache during LLM inference from 16-bit down to 2.5-3.5 bits per channel with near-zero quality loss. Real research, real working community implementations as of April 2026.

**What it actually does for us on a 3060 12GB:**
- Frees up VRAM during inference by shrinking the KV cache (NOT model weights)
- Most useful at long context lengths where KV cache dominates memory
- For a 7B model at 8K context: incremental benefit
- For a 7B model at 50K+ context: meaningful benefit, may make use cases viable that weren't before (e.g. feeding all 800 MCP tool descriptions into one inference window)

**What it does NOT do:**
- Does not compress model weights (use GGUF Q4_K_M / AWQ / GPTQ for that, separately)
- Does not let a 30B model fit on a 12GB card
- Does not help with fine-tuning workloads (training, not inference)

**Verified repos to track (NOT the ones in some AI summaries floating around):**
- `tonbistudio/turboquant-pytorch` — from-scratch PyTorch reproduction, well-documented including bugs found
- `scos-lab/turboquant` — engineering insights on real models, K/V ratio analysis
- `0xSero/turboquant` — Triton + vLLM integration, production-oriented
- `back2matching/turboquant` — pip-installable HuggingFace drop-in (per tonbistudio README)
- `ggml-org/llama.cpp` discussion #20969 — production llama.cpp integration in progress, watch for merge to main

**When to actually try it:**
- After GPU is installed AND working with standard quantized inference
- Wait for llama.cpp to merge mainline support (single CLI flag use is much less work than building from research forks)
- Use case: Sentinel signal extraction over long context windows of MCP tool descriptions

**Honest skepticism notes:**
- tonbistudio repo had a public correction: an earlier "18/18 perfect generation at 5x compression" claim was based on a bugged test where compression wasn't actually happening
- Independent benchmark reproduction is still limited as of April 2026
- Don't believe headline "6x memory reduction" as applied to whole-model inference; it's KV-cache-specific

### ASI-Evolve

See earlier session notes. Same caveats apply: framework is plausible, headline numbers (105 SOTA architectures, +18 MMLU) need paper-level verification, single 3060 can run proof-of-concept experiments only.

## Pattern note for future-Robin

The shopping pattern today surfaced multiple wrong listings before landing on the right one:
- OptiPlex SFF (form factor wrong)
- HP 600 G5 marketplace listing (spec sheet errors, seller unreliable)
- XPS 8930 (PSU ambiguous, Dell proprietary form factor)
- Various NVMe listings (marketplace sellers, wrong interface, no-name brands)

Lesson: "cheapest in category" filtering produces wrong matches when the use case has specific structural requirements. For workstation tower: filter on form factor (Tower not SFF), PSU wattage published, GPU-capable chassis, reputable refurbisher (PCLiquidations, Dell Refurbished Outlet, Newegg-direct, NOT marketplace third parties).

Also noted: I was anchored on pre-crisis NVMe prices and gave Robin stale price expectations earlier in the session. Corrected mid-conversation when search confirmed the AI-driven memory crisis context. Lesson banked: verify market context before quoting prices, especially in volatile categories.

Third pattern noted: AI-generated tech summaries (like the TurboQuant overview Robin pasted) frequently mix real research with fabricated repo names, overstated headline claims, and plausible-looking but non-functional code examples. Always verify against the actual paper, the actual repos, and reproductions — not the summary.