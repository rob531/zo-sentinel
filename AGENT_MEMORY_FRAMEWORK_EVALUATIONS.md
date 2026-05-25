# Agent Memory Framework Evaluations

**Date:** 2026-04-20  
**Status:** Evaluated agentdb, Letta, Mem0, Zep, LanceDB. Adopting none. Build in-house against existing infrastructure.

---

## Headline finding

Five popular agent-memory frameworks were suggested for ZOMesh today via Gemini summaries. All five are real and mature. **None of them are wrong tools — they're tools designed for different problem shapes than ZOMesh has.**

The recurring Gemini pattern: each suggestion is real technology, well-regarded in its actual use case, pitched as solving your problem, but actually designed for a different problem. The summaries pattern-match on surface similarities ("you have agents" → "here's an agent framework") without examining whether the underlying problem shape matches.

## Frameworks evaluated

### 1. AgentDB (ruvnet)

- **What:** npm package, SQLite + HNSW + query helpers + RL scaffolding + alpha cryptographic attestation
- **What it actually is:** SQL query patterns with branded class names, dressed up in jargon
- **Relevant to us?** No. We already have the data structures it would provide. Build in-house helpers instead.
- **See full evaluation below.**

### 2. Letta (formerly MemGPT)

- **What:** Agent runtime with Core/Recall/Archival memory tiers, Apache-2.0, 15K+ GitHub stars, designed around the MemGPT paper's "LLM as OS" concept
- **Designed for:** Long-lived conversational agents hitting context window limits, needing to manage their own context across extended dialogue
- **ZOMesh reality:** Short-lived task-oriented agents scheduled by an external system, bounded 5-30 second inference calls. Managing Core/Recall/Archival tiers for short runs doesn't make architectural sense.
- **Verdict:** Wrong problem shape. BUT one pattern is worth stealing (see "Patterns worth borrowing" below).

### 3. Mem0

- **What:** Memory layer that auto-extracts entity facts, framework-agnostic bolt-on
- **Designed for:** Agents interacting with many end-users, needing to extract and track per-user facts across sessions ("Robbie prefers Adidas")
- **ZOMesh reality:** Internal-only system with no end users. Our "entities" are MCPs, tracked in `mcp_server_registry` + `mcp_registry_facts` with domain-specific schema that fits better than a generic entity store would.
- **Verdict:** Wrong problem shape. Our entity modeling is already more specific than Mem0's generic.

### 4. Zep

- **What:** Temporal knowledge graph service (uses Graphiti underneath), focuses on "how facts change over time"
- **Designed for:** Production chat applications where temporal fact evolution matters ("user's preferences changed", "revenue metric was redefined")
- **ZOMesh reality:** Analytical pipeline where facts are relatively stable. Some MCP state transitions happen (a safe package gets compromised, a reference server gets archived) but those are already tracked via `first_seen`/`last_seen` timestamps and `mention_status` fields.
- **Verdict:** Overkill. Handful of state transitions per week doesn't justify a temporal knowledge graph.
- **Note:** The Gemini summary claimed Zep uses an embedding model called "Bredge." That's hallucinated. It actually uses **BGE-m3** from BAAI. Consistent pattern: specific technical names remain high-risk even when broader summaries are accurate.

### 5. LanceDB

- **What:** Open-source local-first vector database built on Lance columnar format
- **Designed for:** Vector search at millions-of-rows with multi-modal data, embedded or serverless
- **ZOMesh reality:** 1,900 memory entries. LanceDB shines at millions. SQLite + sqlite-vec handles our scale comfortably.
- **Verdict:** Wrong scale. Also a category error in the Gemini comparison — LanceDB is a vector DB, not an agent memory framework. Comparable to Qdrant/Weaviate/Chroma, not to Letta/Mem0/Zep.
- **Caveat:** If we ever hit 100K+ memories AND have specific vector-query patterns that SQLite can't handle, LanceDB is a legitimate upgrade path. Not now.

## The problem-shape matrix

| Framework | Designed problem | ZOMesh problem | Fit? |
|---|---|---|---|
| Letta | Long-lived agent context management | Short-lived task runs | No |
| Mem0 | Multi-user entity fact extraction | Internal domain-specific entities | No |
| Zep | Temporal fact evolution in chat | Stable analytical facts | No |
| LanceDB | Million-row vector search | 1.9K memory entries | No (yet) |
| AgentDB | General agent memory (SQLite underneath) | Already have SQLite | No |

## Patterns worth borrowing (even without adopting frameworks)

### From Letta: The Core/Recall/Archival tiering concept

Our agents' prompt-building currently doesn't distinguish between:
- **Stable identity** ("you are t1.wealth_execution, a trading-analysis agent")
- **Recent relevant context** ("here's what you observed in the last 3 runs")
- **Searchable historical knowledge** ("if you need precedent for X, query past lessons")

Letta's three-tier concept maps to this as:
- Core = the agent's role/persona (always in context)
- Recall = recent relevant memories (injected when relevant)
- Archival = searchable backstore (queried on demand via the in-house retrieval helpers we've already scoped)

The conceptual clarity is useful even if the implementation stays in-house.

### From Zep: Temporal validity windows for facts

When we eventually start tracking "this MCP was safe until date X, compromised after" explicitly, Zep's validity-window concept is a good design reference. Adds columns `valid_from` / `valid_until` to the trust-association tables, lets queries ask "what did we think about this MCP on date X?" Not needed today. Worth noting for the future.

### From Mem0: Auto-extraction of facts from conversation

The pattern of "LLM reads a conversation, extracts structured facts, writes them to memory" is actually what our `data_velocity` agent is already doing when it generates `learning_example` entries. We're doing this, just without the Mem0 branding. Worth being aware of when reading blog posts about Mem0 — recognize the pattern, note that we already have it.

## What we're doing instead

Build three helpers against existing `mesh_memory` + `agent_runs` + `agent_outputs` tables:

1. **`agent_query_past_actions(agent_id, context_tags)`** — returns this agent's past learning_example entries matching current context. Keyword match initially, upgrade to sqlite-vec embedding similarity when we hit 2K memories.

2. **`agent_success_rate_for_action(agent_id, action_cluster)`** — SQL view joining agent_runs status with learning_example content, returns `{n_attempts, n_success, rate, last_failed_reason}`.

3. **Prompt-building integration** — inject past-action results and success rates into the T1/T2/T3 system prompts automatically. Structure as Letta-inspired Core/Recall/Archival tiers:
   - Core: agent role/persona (existing)
   - Recall: top-3 relevant past lessons via Helper 1 (new)
   - Archival: success-rate summary via Helper 2 (new)

Estimated implementation: afternoon of work. Uses existing infrastructure. No migration. No alpha software. No runtime boundary.

## When to reconsider external frameworks

Specific triggers that would change this conclusion:

**Letta:** If we ever build a long-lived user-facing conversational agent (e.g. a CISO advisory agent that takes weekly meetings and remembers context across months). Not in current roadmap.

**Mem0:** If we ever build a product for end users where per-user fact extraction matters. Not in current roadmap.

**Zep:** If temporal fact evolution becomes a first-class concern — e.g. if Sentinel needs to answer "what did we think about this MCP 6 months ago vs now" as a routine query. Worth considering when we have >100 state transitions per week.

**LanceDB:** At 100K+ memories OR when specific vector-query patterns (multi-modal, versioned) exceed sqlite-vec capabilities. Not before.

**AgentDB:** Only if the in-house helpers hit scale limits AND sqlite-vec is insufficient AND we specifically want features like causal graph traversal or cryptographic attestation. Unlikely.

## Durable lessons about "agentic infrastructure" packages

Many LLM-adjacent memory/agent packages marketed in 2025-2026 share patterns:

1. **Most use SQLite or SQLite+vector-extension underneath.** Marketing obscures this.
2. **"Novel" features are often SQL query patterns with branded names.** ReasoningBank, CausalMemoryGraph, ExplainableRecall — all implementable in SQL.
3. **ML algorithm scaffolding (RL, Decision Transformers) is often bundled but rarely used in the core flow.** It's marketing surface.
4. **Adoption has hidden costs:** migration, runtime boundaries, dependency risk on single-author ecosystems, alpha-software exposure for the interesting features.

**First-question discipline when evaluating any such package:**

1. What's the storage layer? SQLite/sqlite-vec → most value is re-implementable. Dedicated vector engine → genuine infrastructure, evaluate seriously.
2. Does the package's *designed problem* match your actual problem? (Not the surface description, the underlying shape.)
3. What's the cost of replicating the value in-house? Often: a weekend of SQL + prompt-engineering work.
4. Is the package maintained by a team or a single person? Single-author ecosystems are higher-risk.

For ZOMesh at current scale, the answer is almost always: in-house, against existing SQLite.

## Durable lessons about AI-generated tech summaries

Today I pressure-tested four separate Gemini/Claude summaries (TurboQuant, agentdb, Letta/Mem0/Zep/LanceDB, and this one). Pattern observations:

1. **Package names hallucinate even in otherwise-accurate summaries.** Examples today: TurboQuant `TheTom/turboquant_plus` (not real), agentdb called `agentic-db` (wrong hyphenation), Zep's "Bredge" embedding model (it's BGE-m3).
2. **Capabilities get inflated but not invented.** "6x memory reduction" (real but misleadingly framed), "150x faster search" (real but with caveats), "outperforms MemGPT" (true on specific benchmarks).
3. **Category errors are common.** LanceDB grouped with agent frameworks despite being a vector DB; TurboQuant framed as model compression despite being KV-cache-only.
4. **Problem-shape mismatches are NEVER surfaced.** The summaries always pitch the tool as fitting your needs, never ask whether your needs match the tool's design assumptions.

**Calibration rule:** when pressure-testing AI summaries about tools, the order of skepticism is:
1. Specific identifiers (package names, model names, version numbers) — highest risk, verify first
2. Capability claims with specific numbers — verify against primary sources
3. Problem-fit assertions — usually the least interrogated; assume the match is asserted, not proven

## Bottom line

**Don't adopt any of these frameworks for ZOMesh right now.** Build in-house helpers against existing infrastructure (`mesh_memory`, `agent_runs`, `agent_outputs`). Steal the Core/Recall/Archival conceptual framing from Letta when structuring prompt-building.

Re-evaluate each framework only if its specific trigger materializes. Otherwise, these are solved problems with existing infrastructure at current scale.

---

## Historical: full AgentDB evaluation from earlier today

(Previous analysis retained for reference — see three-pass iteration where the user's skeptical SQLite question forced the right framing.)

**AgentDB storage:** SQLite (confirmed from npm listing).

**AgentDB features that matter:**
- HNSW vector index (marginal vs sqlite-vec at our scale)
- Pre-written query helpers for common agent-memory patterns
- RL algorithm scaffolding (needs reward signals + training compute)
- Cryptographic attestation (v3 alpha, solves multi-tenant problems we don't have)

**ZOMesh already has:**
- 479 agent_runs with duration, status, tokens, model, error
- 200 learning_example entries with `{cluster, action, lesson}` tuples (IS a proto-ReasoningBank)
- 365 behavioral_pattern entries of agent self-observations
- 54 build_artifact + 3 build_traceback entries

**Three specific gaps:** retrieval during reasoning, formal reinforcement signal, success-rate aggregation. All implementable in SQL.

**User's sharp observation:** "It only uses sqlite underneath, is it just relational queries parsing out failure runs and wiring successes?" Answer: substantially yes. Most "cognitive databases" in the NPM ecosystem share this property.

**Credit to user for staying skeptical through multiple iterations.** Their instinct was correct each time.