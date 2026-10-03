# FINDING 2026-10-02 — the ledger, MEM MCP and graphify KL hold the same facts; lanes are told to write to 6–9 stores

Chairman question (Robin, 2026-10-02): the ledger was meant to take over intra-task comms, graphify KL was meant to integrate memory vectors with the ledger, and MEM MCP is the third store. Do task outputs overlap or over-egg? Measured on the tower and the zo host, ~21:20–21:40Z.

> Disposition (2026-10-03): registered as GR-19..GR-22 and docked decision
> "Funding" in `LOCO_CHAIRMAN.md` §6; store roles adopted as §11.3; new gap
> class GC-14. Numbers below are the 2026-10-02 measurement — re-measure before
> citing them in a grade claim (§11.2 read rule).

## What each store actually holds today

| Store | Size | Content, measured | Writers | Named in lane prompts (37 snapshot 2026-10-02) | `_tools` files touching it (of 604) |
|---|---|---|---|---|---|
| **FOLLOWUPS.md (ledger)** | 5.05 MB, 16.8k lines | 577 FU blocks (306 resolved, 240 open, 41 done, 13 in-progress, 11 pr-open); 2,138 dated log lines, **~29/day** in the last 7 days | every lane + fu-verify | 27/37 | 180 |
| **MEM MCP** (`.claude/projects/.../memory`, index.db **1.16 GB**) | 2,863 indexed files | `memory` type = 650 files: **577 are a verbatim per-FU explode of FOLLOWUPS** (`explode_followups_to_memory.py`, 579 rewritten in the last 24 h) + **34 hand-written nodes**; `transcript` = 2,080 session transcripts = **229k of 235k chunks (97%)**; `doc` = 131. Paraphrase recall@10 measured 0.40 on 09-09 | exploder (daily) + lanes by hand | 17/37 | 17 |
| **graphify KL** (zo `graphify-out/`) | graph.json 636 MB, dir 2.2 GB | 602,970 nodes; source files by directory: `directives` 814k refs, `services` 269k, `quarantine` 44k, `lessons` 12.7k, `tests` 11k — a **code/AST graph of builder output**. Ledger integration is `_fu_index.json`: **194 of 577 FUs** anchored to files, **130 of 194 drifted**. The string "FU-" appears 220 times in 636 MB. **No memory vectors in it.** | graph_refresh daemon + graphify-kl-daily-refresh lane | 17/37 | 46 |

And the stores nobody listed: Claude project memory (150+ scars, auto-written after sessions), 127 project docs (~6/day, mostly a lane's account of its own run), `friction_ledger.jsonl` (1,089 rows), `hazards_registered.jsonl` (70), `peer_decisions.json` (2.4 MB) + `.jsonl` (66 MB), `AUTOPOIESIS.md`, GitHub chairman issues.

## Overlap — yes, and it is structural, not accidental

- One FU fact today lives in: its FOLLOWUPS block → its MEM MCP explode copy → (for 194 of them) a graphify anchor → usually a `CYCLE_*` project doc → often a project-memory scar → a friction row → sometimes a GitHub issue. **Five to seven copies, one of which is the writer.**
- The copies are not free: the exploder rewrites 579 files a day (busting the index every day), graphify re-walks 110,985 files daily to keep 194 anchors, and MEM MCP's 1.16 GB index is 97% transcripts — it is a transcript search engine with a copy of the ledger attached, which is why it recalls by spelling.
- The duplication is **ordered by the prompts**: 17 of 37 lane prompts name six or more stores (histogram: 0 stores ×7, 1 ×10, 2 ×2, 3 ×1, 5 ×1, 6 ×5, 7 ×9, 8 ×1, 9 ×1); average prompt 42.6k chars. Each lane is told to record its run in the ledger, friction, MEM MCP, a scar, AUTOPOIESIS.md, peer_review and a project doc. The volume Robin cannot review is the sum of those instructions, and autopoiesis cannot shrink it because the prompts are its own standing orders and a lane may not edit them.
- graphify did not become "memory vectors + ledger". It became the builder's code graph (which is useful for the builder: imports, anchor drift, spine) with a thin FU index bolted on. Treating it as a memory is what makes `graphify-kl-daily-refresh` a 40k-char prompt.

## Proposed roles (one writer per fact)

1. **Ledger = the only record of fact.** Per-FU records, FOLLOWUPS.md regenerated (S24 in the rebase chain). Log line ≤ 600 chars.
2. **MEM MCP = a derived index, never hand-written.** It indexes the ledger, the project docs and transcripts; the 34 hand nodes move into FOLLOWUPS or a project doc; the daily full re-explode becomes incremental (only changed FUs). Lanes read it; lanes do not write it.
3. **graphify KL = the builder's code graph.** Keep anchor drift and spine; drop the ambition that it is a memory; the daily-refresh lane shrinks to the zo cron it already duplicates (plan line 6).
4. **Project docs + project memory = chairman-facing only**, fed by the one-pager; cycle narratives stop being project docs (they are ledger log lines).
5. **Lane prompts name one write target** (the ledger) plus friction for hazards. This is K1 (lane-prompt audit) with teeth; it is also the output budget.

## Cost (what the chairman has to know)

- $250 cloud credit: expires 2026-10-08; spend **unknown** (no CREDIT_LEDGER.md), estimated $80–150 across 4 sessions.
- Anthropic API ≈ $1 (09-29), Mistral out, vast ≈ $5.5 (09-29). No monitor reads any of these; the builder has already "died of money" once with no red line.
- Dependent work: auth_evidence E14b needs ≤ $6 Anthropic; E16 ≤ $1.50 vast; the weekly moat wave ceiling is $3 vast. Everything else on the slate is tower/zo compute at $0.

## Go / no-go for Robin (answer each with go, no, or "have the lanes evaluate")

1. Store roles 1–5 above become the standing rule; K1 rewrites the 19 enabled prompts to one write target.
2. Stop the daily full explode; MEM MCP goes derived-only.
3. graphify scoped to builder code graph; drop "KL as memory".
4. Build `chairman_one_pager.py` + `creds_runway.py` as deterministic tower tasks.
5. PR to zo-fleet-tools `alert_registry.json` (dead `last_zm_go` and `syncthing` checks).
6. Funding: top up Anthropic ~$20 and vast ~$25 **only** to finish E14b/E16 and keep the weekly wave — or let those chains record STOP.
7. Start CREDIT_LEDGER.md (one line per cloud session) so item 6 and the 10-08 expiry are decidable.

Robin, 2026-10-03: "Take this on and resolve our duplicated lanes and overly verbose findings — anything complex hand to FABLE for resolution." Read as GO on 1–5 and 7; 6 is money and stays a docked decision.
