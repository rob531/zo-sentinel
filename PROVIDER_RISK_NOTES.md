# Provider Pricing Risk Notes

**Created:** 2026-04-20  
**Purpose:** Track LLM API pricing trends and document fallback positions if primary providers become uneconomic. Refresh quarterly.

---

## Current state (April 2026)

Verified via web search 2026-04-20. Prices change fast — re-verify before relying on these.

**Anthropic:**
- Claude Haiku 4.5: $1 input / $5 output per million tokens
- Claude Sonnet 4.6: $3 input / $15 output per million tokens (with 1M context, no surcharge)
- Claude Opus 4.6: $5 input / $25 output per million tokens (with 1M context, no surcharge)
- 90% off cached input tokens
- 50% off batch API
- Power user subscription: Pro $20/mo, Max $100/mo (5x usage), Max $200/mo (20x usage)
- BYOK status: currently 100% failing for our use case (separate auth/integration issue, not pricing)

**MiniMax (current primary inference for ZOMesh builder):**
- $10/month flat rate
- Zero observed 429s across weeks of use
- Lower model quality than frontier but adequate with Ollama classifier filtering upstream
- Risk profile: Chinese provider, exposed to potential US market exit OR rate-limit changes more than to per-token price hikes

**Local Ollama (free):**
- phi3:mini for routing
- llama3.2:3b for fallback
- Zero marginal cost, limited by ZoComputer compute

## Trend signals

**Downward (still dominant overall):**
- Token prices dropped ~10x over last 2 years
- New model generations typically cheaper than prior ones at same capability tier
- OpenAI and Google aggressively cutting prices on standard tiers

**Upward (specific regimes only, not universal):**
- OpenAI introduced context-length surcharges (GPT-5.4 doubles input pricing above 272K tokens)
- New "max" subscription tiers ($100-$200/mo) didn't exist 18 months ago — effective price increase for heavy users
- Anthropic historically holds prices longer than competitors — most exposed to a future rate increase if any major provider raises
- AI hardware shortages (NAND/DRAM crisis, GPU supply) creating cost pressure on hyperscalers that may flow to API rates over 6-12 months

## Risk register for Sentinel/ZOMesh

| Risk | Likelihood | Impact if it happens | Today's mitigation |
|---|---|---|---|
| MiniMax raises flat rate 2-3x | Low (12mo) | Low — still cheaper than alternatives | Switch to next-cheapest flat-rate provider |
| MiniMax exits US market | Medium (12mo) | Medium — lose primary inference path | Fall back to Ollama + Claude Haiku for routing |
| MiniMax imposes hard rate limits | Medium (6mo) | Medium — builder throughput drops | Re-enable conservative builder pacing, expand Ollama use |
| Anthropic Sonnet/Haiku rates double | Low (12mo) | High for BYOK use cases | Once GPU is local, fine-tune small models for tool_description_safety + permission_scope, reduce API dependency |
| Anthropic introduces context-length surcharges | Medium (6mo) | Medium — affects long-context Sentinel work | Use chunked inference, leverage prompt caching (already 90% off) |
| GPU shortage prevents tower buildout | Already happening | Medium — delays signal-fine-tuning roadmap | Buy used 3060 from r/hardwareswap when one surfaces, defer until then |
| ZoComputer service degrades or shuts down | Low (12mo) | Critical — entire mesh hosted there | Tower (purchased today) provides eventual local-host fallback |

## Contingency: "What we'd switch to if Anthropic doubled prices tomorrow"

Sequenced fallback plan:

1. **Immediate (within 24h):** Stop all BYOK Sonnet/Opus calls. Route everything through MiniMax + Ollama.
2. **Within 1 week:** Audit which signals/tasks genuinely need frontier-quality inference vs. which were on Sonnet by default. Most are the latter.
3. **Within 1 month:** Migrate any retained Anthropic usage to Haiku 4.5 ($1/$5) which is half the marginal cost of Sonnet at 70%+ of the quality for our tasks.
4. **Within 3 months (assumes tower has GPU):** Begin fine-tuning small specialist models for tool_description_safety + permission_scope, removing those signals from API dependency entirely.
5. **Within 6 months:** Local 7B-13B model serving most routine inference. API used only for genuinely novel reasoning tasks.

**Estimated quality impact of full fallback:** moderate degradation for ~3 months while fine-tuning corpus is built; equivalent or better quality after that for the specific signal extraction tasks (specialized models on labeled data tend to outperform generalist frontier models).

**Estimated cost trajectory:** monthly inference costs drop from current $X to roughly electricity-only ($35-70/mo NYC) plus occasional API calls.

## Things to track

Review this doc quarterly. Specifically watch for:

- Anthropic pricing-page changes (especially Sonnet 4.6 / Haiku 4.5 base rates)
- MiniMax US service status
- Major model releases that might change capability/cost frontier (e.g. an open-source 30B model that matches Sonnet on signal-extraction would let us drop API entirely)
- llama.cpp + TurboQuant mainline support (would meaningfully reduce VRAM requirements for long-context local inference, making 12GB GPU more useful)
- AWS Bedrock / GCP Vertex pricing for the same models (sometimes cheaper than direct API for committed-use tiers)

## What this means for the hardware decision

The tower purchase ($377 today) was made on the strength of:
1. Directive-loop productivity unlock (primary)
2. Signal fine-tuning capacity (secondary, deferred until GPU)
3. Insurance against provider/platform changes (tertiary)

This doc updates the weight of #3. As of April 2026, provider pricing is mostly stable-to-falling, but the specific risks above are real and have a 6-12 month horizon. The tower is correct insurance regardless of which way prices move; the GPU upgrade becomes more urgent if Anthropic pricing trends reverse.

**No action today.** Re-read this doc when GPU/storage prices look more favorable, or when any of the watch-list events materialize.