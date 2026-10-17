# Eval: typed decisions (TypeSafe Jev) for the builder and the /app UI

2026-10-17 · cloud session · trigger: The New Stack, "Workday is using TypeSafe's Jev to build user interfaces on the fly" (2026-10-13)

## The idea in one paragraph

Workday's Sana does not have an LLM *write* a screen. It hands Jev — a "System One"
model that only answers closed questions (Choice up to 255 options, ordered Score,
yes/no Noul, each with probabilities, never free text) — the user's request plus
every atomic component in its design system, and Jev *picks*. Reported latency
70–500 ms; reported price ~$0.042 per million input tokens with output free. One
practitioner reported a generative dashboard going from 29 s to 3.8 s median by
routing closed questions to Jev and sending sub-50%-confidence answers to an LLM
(synthetic data). Jev's own docs list weaknesses: counting, arithmetic, dates, a
bias toward the first-listed option, and **it does not treat injected content as
hostile**. Hosted-only (TypeSafe API or OpenRouter), US processing.

## What the repo looks like against that pattern (measured, HEAD 1fd0a6f)

- **No structured-output enforcement exists anywhere.** Zero `response_format`,
  JSON-schema or constrained decoding. Every discrete LLM decision is free text
  scraped for braces (`pi_quarantine_reviewer.parse_llm_response`,
  `sentinel_directive_generator`, `escalation._parse_*_tool_calls`).
- The architect fails closed choices today: `directive_mcp.py:646` stamps
  `recipe=module_from_exemplar` server-side because "the weak MiniMax architect
  ignores the prose". That is exactly the decision class Jev is built for.
- Discrete decisions currently made by keyword heuristics: `goose_runner._select_recipe`,
  `ask_question_intent_routing_api.classify_intent` (regex + hard-coded confidences),
  staging-drain classification, PROMOTE/HOLD.
- `/app` (`app/static/app.html`) already has a fixed component vocabulary: five
  view functions plus the vulns panel, dispute form, axis bars, badges and stat
  cards. `render()` is a hard-coded path switch — the natural insertion point.
- The axis scorer is *already* Jev-shaped: per-axis label + probability vector
  (`McpLlmAxisScore.probs`, `p_top`) from classification heads.

## What was built in this session (PR on branch `eval/jev-typed-decisions`)

| File | What |
|---|---|
| `zo_sentinel/decide/core.py` | Vendor-neutral contract: `Choice/Score/Noul`, schema limits (≤255 options, 2–10 levels, 32k-token state guard), **out-of-schema answers raise, never pass on**, first-class abstention below a per-question floor, optional order-debias (answer twice, options reversed, average). |
| `zo_sentinel/decide/backends.py` | `LexicalBackend` (offline, free baseline) and `JevBackend` (documented decisions shape; model pinned to `typesafe/jev-1.13`; key from env only — AgentVault — refuses without one). |
| `zo_sentinel/decide/ui_compose.py` | Sana-style composer for `/app`: options are only views/panels that exist; unsure → today's search view; `detail` without an identified server → search. |
| `tools/decide/eval_decide.py` + `ui_eval_set.jsonl` | Harness: UI suite (40 synthetic labelled requests: coverage, accuracy, ECE, cost, latency) and recipe suite (530 real directives; agreement with `_select_recipe` only — there is no ground truth). rc 2 when it cannot run. |
| `tests/test_decide_typed.py` | 12 tests; the schema-rejection and debias tests were each mutated and observed RED before being restored. |

Nothing calls the module yet. No paid API calls were made: no Jev/OpenRouter key
exists in the cloud session, and secrets are not moved into it by design.

## Baseline numbers (lexical backend, offline)

- UI suite: coverage 0.10, accuracy-when-answered 1.00, end-to-end view 0.375,
  risk filter 0.775, panels 0.825, ECE 0.35. It abstains on 90% and degrades to the
  current page — the safety property holds; the selection quality is what Jev must buy.
- Recipe suite: 98/530 answered; 0.50 agreement with the keyword heuristic.

These are the bars. Jev is worth wiring only if, on the same harness, it reaches
**≥0.85 end-to-end view accuracy with ECE ≤0.10 and p50 ≤500 ms** on the UI suite.

## Cost model (at the reported price — verify on the OpenRouter model page before spend)

| Use | Tokens/call | Calls | Est. cost |
|---|---|---|---|
| One /app composition | ~500 | 1 | ~$0.00002 (matches the documented example: 476 tokens → $0.00002) |
| Full UI eval (40 rows, debias) | ~500 | 80 | < $0.01 |
| Recipe shadow over all 530 directives | ~400 | 530 | ~$0.01 |
| Hypothetical 7-axis pass over 20k MCPs | ~4k | 20k | ~$3.40 |

The money is not the constraint. Trust boundary and data egress are.

## Verdict by surface

1. **ADOPT now — the contract, not the vendor.** `zo_sentinel.decide` fixes a real,
   standing defect (no enforced schema on any discrete decision) with zero vendor
   dependency. Next step: a `LadderBackend` that asks `escalation.ask` for a
   probability map and lets `decide()` reject anything out of schema.
2. **TRIAL Jev in shadow on trusted-input surfaces.** (a) `/app` view composition —
   input is the analyst's own text; (b) builder recipe/handler/complexity selection —
   input is our own directives. Shadow = log decisions next to the current
   heuristic, change no behaviour, until the bars above are met.
3. **HOLD for scoring and the PI quarantine reviewer.** Both feed attacker-controlled
   text (MCP READMEs, tool descriptions, injection payloads) to a model that the
   vendor says does not treat injected content as hostile. A server could argue its
   own trust score up. At most a sanitised, minority ensemble vote, never sole
   authority. Also: CLAUDE.md allows outbound HTTP only via the inference router,
   so any Jev call must go through the mesh egress (`JEV_BASE_URL`), not direct.
4. **The bigger lesson for the builder: pick, don't generate.** 67 active vs ~2,100
   staged services is the cost of letting a model write whole files. The Sana move
   applied to the builder is a registry of exemplar parts (`services/_exemplar`,
   recipes, routers) where the architect *chooses* parts and parameters through
   typed questions, and generation is confined to narrow slots. That is an ADR-001
   Stage 2 ("contract-uniform builder steps") input, independent of Jev.

## Decisions needed from the chairman

- Approve one OpenRouter (or TypeSafe) key into AgentVault with a hard spend cap
  (~$5 is ample for months of shadow evals). Without it, step 2 cannot run.
- Data: only analyst queries and our own directives would be sent; no customer or
  firm data. TypeSafe offers zero-retention to enterprise customers only.

## Sources

- The New Stack, 2026-10-13 (Workday/Sana, latency, Banking77 comparison, limitations)
- OpenRouter Jev tutorial (request/response shape, endpoint, pricing on input only)
- promptfoo TypeSafe provider docs (model pinning, 64k request / 32k state limits)
- developersdigest.tech Jev release guide (per-token price, 70–500 ms claim) — secondary
