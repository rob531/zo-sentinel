# Retry & Reasoning-Mode Gap Sweep — 2026-04-21

**Context:** today's directive generator rescue surfaced two underlying patterns that likely exist elsewhere in the mesh. This is the quick sweep to identify and prioritize follow-up patches.

## The two patterns

**Pattern A — missing retry+backoff on external/service calls.**  
`http_retry.post_with_retry()` exists in the codebase (in `ALREADY_BUILT`) but most call sites still use raw `requests.post()` with no retry. Transient failures (rate limits, 5xx, network hiccups, WriteService bounces during `zm go`) that should self-heal instead cause whole cycles to fail.

**Pattern B — incomplete reasoning-mode preamble stripping.**  
MiniMax-M2.7 began returning `<think>...</think>` preambles by default mid-April 2026. Some call sites have partial defenses (strip balanced tags only), some have none. Unclosed-tag and truncated-reasoning cases fail both.

## Files scanned today

### `/home/workspace/zo_sentinel/sentinel_directive_generator.py`
- **Status: PATCHED (2026-04-21)**
- Added `_strip_reasoning_preamble` handling balanced + unclosed tags, 4 tag variants (think, reasoning, thinking, analysis)
- Migrated `call_minimax` and `call_ollama` to `post_with_retry`
- Timeouts: 120s → 240s (both)
- Retries: 0 → 2 (both)
- First post-patch cycle: MiniMax returned in ~94s with `<think>` preamble, stripper fired, 6/6 directives parsed and queued. Confirmed working.

### `/home/workspace/zo_mesh/zo_sentinel_builder.py`
- **Status: GAP — follow-up recommended**
- Has `minimax_strip_think` but only handles balanced `<think>...</think>` tags. Would fail on unclosed or truncated reasoning output.
- Has `ollama_strip_thinking` for Ollama's `<thinking>` variant (separate pattern).
- `minimax_generate`, `ollama_generate`, `ws_query`, `ws_write`: NO retries. Single `requests.post`.
- MiniMax timeout: 120s. Ollama timeout: 150s.
- `max_tokens=8192` (higher than directive gen's 4096 — more room for output, but also more reasoning-token budget to burn through).
- **Critical path right now:** 6 directives just queued from the first post-patch directive-gen cycle. Builder will pick them up on next 5-min poll. Each directive triggers a MiniMax call with same failure modes.

### `/home/workspace/zo_sentinel/signal_bridge.py`
- **Status: GAP — lower priority**
- `ws_query`, `ws_write`, `heartbeat`: all raw `requests.post`, no retry.
- Calls are to local WriteService (127.0.0.1:8772), so timeouts should be short (5-10s) with more retries (3-5) and shorter backoff (0.5s, 1s, 2s) — tuned differently from external-API calls.
- Visible symptom in logs: the `fetch enrichments failed: 400 Client Error` and `HTTPConnectionPool... Connection refused` errors from 2026-04-20 that persisted across WriteService bounces. With retries, these would self-heal silently.

## Files not yet scanned (likely have same gaps)

- `/home/workspace/zo_sentinel/ecosystems_metadata_fetcher.py` — external API (packages.ecosyste.ms), 6h cycle; if a fetch fails, whole batch lost until next cycle
- `/home/workspace/zo_sentinel/threat_intel_ingestor.py` — multiple external feeds
- `/home/workspace/zo_sentinel/otx_ingestor.py` — AlienVault API
- `/home/workspace/zo_sentinel/mcp_registry_ingestor.py`
- `/home/workspace/zo_sentinel/mcp_reference_servers_ingestor.py`
- `/home/workspace/zo_mesh/mesh_guardian.py` — bridge between T1/T2/T3 tiers
- `/home/workspace/zo_mesh/data_velocity_engine.py`
- `/home/workspace/zo_mesh/wisdom_synthesiser.py`
- `/home/workspace/zo_mesh/anti_entropy_daemon.py`
- `/home/workspace/zo_mesh/pipeline_bridge.py`

## Recommended follow-up patches, in priority order

### 1. `zo_sentinel_builder.py` — proactive patch before builder hits the 6 queued directives

Two small changes:

**(a) Replace `minimax_strip_think` with the more robust `_strip_reasoning_preamble` pattern from directive gen.** Same 10 lines, handles unclosed tags + 4 variants. Low risk — strictly a superset of current behaviour.

**(b) Bump MiniMax timeout 120→240s.** One-line change. Matches directive gen config.

Deferred decision: full retry migration for builder (using `post_with_retry`). Higher touch; recommend doing after seeing how the builder handles the next 1-2 cycles with just the stripper+timeout fix. If it's fine, retry migration is a nice-to-have; if transient failures surface, promote to priority 1.

### 2. `signal_bridge.py` — short-timeout, high-retry for local service calls

Use `post_with_retry` with: `timeout=5, retries=3, backoff=0.5`. This is the "recover fast when WriteService bounces" profile. Different from external-API profile.

### 3. Generalized: audit remaining files

For each unscanned file above, check for `requests.post` without `post_with_retry`. If found, apply the appropriate profile:
- **External API** (MiniMax, Ollama remote, threat feeds): `timeout=240, retries=2, backoff=2.0`
- **Local service** (WriteService, InferenceRouter on 127.0.0.1): `timeout=10, retries=3, backoff=0.5`

Rough time estimate: 15 min per file, ~10 files, mostly templated — 2.5h of focused work to close the gap across the mesh.

### 4. Codify in builder_conventions.json

Add two conventions (severity: required) to `/home/workspace/zo_sentinel/builder_conventions.json`:
- `"all_external_api_calls_use_post_with_retry"` with example snippet
- `"all_llm_response_parsers_strip_reasoning_tags"` with the 4-variant regex pattern

Next directive generator cycle will inject these into every build prompt. Future generated code starts compliant.

## Risk assessment for the 6 queued directives

Probability the builder processes all 6 cleanly without builder patches:
- **MiniMax returns within 120s for each:** likely but not guaranteed. The directive gen call just took 94s; a builder prompt is shorter (no full schema/wiring/gaps blocks), so should be faster. Rough estimate: 80% of calls complete in time.
- **MiniMax returns clean balanced `<think>...</think>`:** also likely. Today's observed output had balanced tags. The builder's current stripper handles this case.
- **Combined per-directive success probability:** ~75-80%.
- **Expected outcome for 6 directives:** 4-5 succeed, 1-2 fail with timeout or parse error.

Not catastrophic — builder will retry failed directives on its next cycle, and we have the `auto_restore_failed_directives` logic. But noisy. Applying the builder patches now would bring this to ~95%+ per-directive success.

## What's NOT in scope for this sweep

- Fixing the 400 Bad Request errors in signal_bridge from 2026-04-20 (separate root cause, already investigated)
- World agent memory staleness (14,000+ min old — unrelated, longer-running issue)
- BYOK Sonnet 100% failure rate (separate auth/integration issue per userMemory)
- MCP Registry detail endpoint 404 investigation (pending from yesterday)

## Open question

MiniMax-M2.7 likely accepts a request parameter to disable reasoning mode (common in provider APIs for structured-output use cases). If so, disabling reasoning on directive-gen + builder calls would remove the stripper/timeout concerns entirely for these specific call sites and save cost/latency on every call. Requires testing to confirm what parameter name MiniMax accepts. Deferred until we have a test harness — don't want to experiment in prod.