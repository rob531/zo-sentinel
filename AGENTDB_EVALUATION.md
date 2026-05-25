# AgentDB Evaluation Note

**Date:** 2026-04-20  
**Status:** Deferred. Not adopting. Build in-house alternative instead.

---

## Headline finding

AgentDB uses SQLite underneath. The novel-sounding features (ReasoningBank, CausalMemoryGraph, ExplainableRecall) are SQL query patterns wrapped in branded class names plus an HNSW vector index. For a small mesh with local SQLite state, the SQL patterns ARE the value, and they're cheaper to write in-house than to adopt the package.

The user's skeptical read was correct: "it only uses sqlite underneath... is it just relational queries parsing out failure runs and wiring successes?" Answer: yes, substantially.

## What agentdb actually is

**Storage layer:** SQLite (confirmed from npm listing: `new AgentDB({ dbPath: './memory.db', vectorBackend: 'ruvector' | 'hnswlib' | 'sqlite' })`).

**Value added over raw SQLite:**
1. Pre-written query helpers for common agent-memory patterns (saves afternoon of coding)
2. HNSW vector index for approximate nearest-neighbor search on embeddings (marginal speedup vs sqlite-vec's flat index at small scale)
3. RL algorithm scaffolding (PPO, Decision Transformer, MCTS) — needs reward signals and training compute to actually use; irrelevant for prompt-injection reinforcement
4. Cryptographic attestation (v3 alpha) — solves multi-tenant trust problems we don't have

**Not actually novel:**
- ReasoningBank = table of `{context, action, outcome, lesson}` + similarity query + prompt injection
- CausalMemoryGraph = edges between memory rows with causal labels + graph traversal query
- ExplainableRecall = returning the matched rows alongside the query that matched them (provenance)

All of these are SQL query patterns. The novelty is marketing, not architecture.

## What ZOMesh already has

- **agent_runs** (479 rows, 98 in last 7 days): execution history with duration, status, tokens, model, error
- **agent_outputs** (479 rows): what each run produced
- **mesh_memory.memory_type='learning_example'** (200 rows): `{cluster, action, lesson}` tuples per agent — this IS a proto-ReasoningBank
- **mesh_memory.memory_type='behavioral_pattern'** (365 rows): agent self-observations
- **mesh_memory.memory_type='build_artifact'** (54 rows): code-generation history
- **mesh_memory.memory_type='build_traceback'** (3 rows): debugging paths

Confirmed sample of existing learning_example for t1.wealth_execution:
```json
{"cluster": "cognitive_bias", "action": "mathematical_model_worship",
 "lesson": "...learning rule combining current state and environment feedback...",
 "source": "data_velocity"}
```

The data structure IS what agentdb would store. The infrastructure is real. It's just underutilized.

## What's actually missing (the gap)

Three specific things, all implementable in SQL against existing tables:

### Gap 1: Retrieval-during-reasoning

Agents don't consistently query their own learning_example history when starting new tasks. The data exists but isn't read back into the prompt-building layer.

**Fix (20 lines of Python):**
```sql
SELECT content FROM mesh_memory 
WHERE agent_id = ? 
  AND memory_type = 'learning_example'
  AND (json_extract(content, '$.cluster') = ?
       OR json_extract(content, '$.action') LIKE ?)
ORDER BY created_at DESC
LIMIT 3
```

Wrap in Python, inject into prompt. Done.

### Gap 2: Success-rate aggregation per action-cluster

Currently have `agent_runs.status` but no aggregated success rates per action-type that an agent could query.

**Fix (SQL view + helper):**
```sql
CREATE VIEW agent_success_rates AS
SELECT 
  agent_id,
  json_extract(content, '$.cluster') as cluster,
  json_extract(content, '$.action') as action,
  COUNT(*) as n_attempts,
  SUM(CASE WHEN json_extract(content, '$.outcome') = 'success' THEN 1 ELSE 0 END) as n_success
FROM mesh_memory
WHERE memory_type = 'learning_example'
GROUP BY agent_id, cluster, action;
```

Query this view at prompt-build time. Inject "you've tried X in context Y N times, success rate M%".

### Gap 3: Outcome tagging

Current learning_example entries don't consistently tag outcome (`success` | `fail` | `partial`). Need to either:
- Update writers to include outcome field
- Backfill based on adjacent agent_run.status

Either way, small migration, not new infrastructure.

## Upgrade path when needed

At the ~2000 memory threshold, sqlite-vec activates. Then replace keyword matching in Gap 1 with embedding similarity:

```sql
SELECT content FROM mesh_memory
WHERE agent_id = ?
  AND memory_type = 'learning_example'
ORDER BY vec_distance(embedding, ?) ASC
LIMIT 5
```

Same query shape, better retrieval quality. Still SQLite. Still no new infrastructure.

## Why NOT agentdb

1. **Would re-implement what we already have** (learning_example tuples, execution history)
2. **Migration cost** on ~1,900 existing memories
3. **Runtime mismatch** — ZOMesh is Python-first; agentdb is JS
4. **Single-author ecosystem risk** — ruvnet's entire stack (agentic-flow, agentdb, ruvector) is one person's work
5. **Alpha software** for the features that would justify switching
6. **Kitchen-sink architecture** — 9 RL algorithms, causal graphs, cryptographic proofs, distributed sync; broad surface rarely means best-in-class
7. **80/20 calculus** — in-house helpers get 80% of the value at 5% of the cost

## When to reconsider

Three specific triggers that would make agentdb worth re-evaluating:

**Trigger A: Scale pain.** In-house helpers work but slow down noticeably at 10K+ memories. If sqlite-vec is also insufficient, then dedicated vector engines (Qdrant, Weaviate, agentdb's HNSW) become worth evaluating.

**Trigger B: Causal reasoning requirement.** If Sentinel ever needs to answer "what chain of events caused this MCP to be flagged?" with formal causal graph traversal (not just joined queries), CausalMemoryGraph might be the right primitive.

**Trigger C: Formal audit trail.** If a client matter or regulatory inquiry demands cryptographic provenance for scoring decisions, AttestationLog becomes relevant.

None of these are today. All are contingent future scenarios.

## Durable lesson about "agentic infrastructure" packages

Many LLM-adjacent memory/agent packages marketed in 2025-2026 are:
- SQLite or SQLite+extension underneath
- Plus pre-written query helpers
- Plus ML algorithm scaffolding (often unused in the core flow)
- Plus branded class names for standard patterns

**First question to ask about any such package: what's the storage layer?**

- SQLite/sqlite-vec: 80% of value is re-implementable in a weekend, package saves you the weekend but costs you integration/migration/dependency risk
- Postgres + pgvector: similar, slightly more setup
- Dedicated vector engine (Qdrant, Weaviate, Milvus): genuine novel infrastructure, worth evaluating if you have scale or specific vector-query patterns
- Custom storage engine: rare and mostly research; high risk for production

For ZOMesh's specific scale (thousands of memories, 20-agent mesh, single-host), SQLite + extensions + in-house query helpers is the right answer. The ceiling on that approach is roughly 100K memories and tens of queries per second; we're nowhere near either limit.

## What I got wrong earlier

**First pass:** focused on package-name hallucination and kitchen-sink architecture. Correct but incomplete.

**Second pass:** engaged with the feature (agent self-memory with reinforcement) but didn't surface the key architectural insight clearly enough. Recommended building helpers but framed it as "alternative to agentdb" rather than "what agentdb actually is underneath."

**Third pass (this version):** the user's skeptical question about SQLite forced the right framing. The key insight is that "uses SQLite underneath" means the package's value is primarily query helpers and conventions, which can be replicated in-house at lower cost than migration. Most "agentic infrastructure" fits this pattern.

Credit to user for staying skeptical through multiple iterations of my evaluation. Their instinct that "it's just relational queries parsing failures and wiring successes" is essentially correct.

## Bottom line

**Don't adopt agentdb.** Build three in-house helpers (past-action retrieval, success-rate aggregation, prompt injection) against existing mesh_memory + agent_runs tables. Afternoon of work. Upgrades to embedding-similarity retrieval when sqlite-vec activates at 2K memories.

Re-evaluate agentdb only if Trigger A, B, or C materializes. Otherwise, this is a solved problem with existing infrastructure.