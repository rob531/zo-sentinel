# DESIGN: v1.1 "Perspectives" + v2 "Ask MCPLookup" RAG search

*2026-07-02. Chairman-directed start of the post-launch roadmap (council ruling 2026-06-27;
chairman override 2026-07-02 opens BOTH lanes concurrently). This doc is the Graphify-indexed
knowledge companion to PRODUCT_SPEC.md Appendix F (Perspectives) and Appendix G (Ask-RAG) --
the appendices carry the directive candidates; this doc carries the reasoning, contracts, and
learnings so the architect can embellish follow-on directives from it.*

## Why these two, in this shape

Perspectives and Ask-RAG are the two discovery surfaces over the scored registry (65,532 servers
x 7 axes). Perspectives is the DETERMINISTIC half: an admin builds the taxonomy, users navigate
it; reproducible, governable, zero per-query LLM cost, and the natural unit to attach trust-diff
notifications to ("alert me when anything in MY view changes tier"). Ask-RAG is the APPROXIMATE
half: free-text queries with mandatory provenance. History lesson (HISTO, 6/27): every "more
surface without a grounded anchor" episode collapsed into churn -- so every candidate here is
schema-grounded, exemplar-referencing, self-tested, and individually bounded.

## Ground truth (the hard contract)

- Registry: `mcp_server_registry` (server_id PK, name, registry_source, url, description,
  trust_score FLOAT, verdict, confidence, risk_tier, last_assessed, metadata TEXT).
- Scores: `mcp_llm_axis_scores` (server_id, axis_name, label, label_index, probs JSON, p_top,
  escalated, model_version, scored_at) -- 7 axes: overall_risk, auth_strength (4 classes:
  STRONG/MODERATE/WEAK/UNKNOWN -- schema contract risk_axis_mapping_v1.json, do NOT infer class
  enums from samples), capability_breadth, data_sensitivity, network_egress, maintainer_trust
  (NON-ordinal), exploit_surface. Always filter to the latest/production model_version.
- Trust override: trust_gating_override caps official/ESTABLISHED-maintainer servers below
  HIGH/CRITICAL -- perspective queries and RAG snippets must present the ADJUSTED tier, not the
  raw overall_risk, or the false-positive audit (2026-06-25) repeats in the new surfaces.
- Access: app modules read via the app_scoring_consumer seam / app DB session (Postgres-portable
  SQL) or write_service :8772 (/query, /write). NEVER import duckdb directly; NEVER invent
  data/*.csv sources (schema-PRM blocks it).

## Facet universe (v1 -- real columns only)

facet_key in { risk_tier, verdict, registry_source, trust_band (trust_score quartiles),
axis:overall_risk, axis:auth_strength, axis:capability_breadth, axis:data_sensitivity,
axis:network_egress, axis:maintainer_trust, axis:exploit_surface }.
The original concept named hosting-model and data-residency facets: those columns DO NOT EXIST
yet and are explicitly OUT of v1.1. If they become real (e.g. derived from metadata/url), add
them via facet_enum_service, not by hand in views.

## New tables (created via write_service on first write; keep DDL boring)

- `perspectives`  {id, org_id, name, description, facet_filters JSON, created_by, created_at,
  updated_at}
- `perspective_snapshots`  {id, perspective_id, taken_at, membership JSON [{server_id,
  risk_tier}]}
- `ask_corpus_index`  {server_id, snippet TEXT, terms JSON, indexed_at} + one watermark row.

## Module graph (build order = dependency order)

Perspectives lane: facet_enum_service.py -> perspective_model.py -> perspective_query_api.py ->
perspective_admin_api.py -> perspective_diff_service.py -> perspective_tree_view.html.
Ask lane: ask_corpus_indexer.py -> ask_retrieval_service.py -> ask_answer_api.py ->
ask_search_view.html. Both API routers mount via app_router_registry.include_app_routers.

## Non-negotiable properties (carry into every directive)

1. Determinism default: perspective queries are pure SQL; ask synthesis is templated; LLM polish
   only behind ASK_LLM=1 via ladder_shim:8796 (cost ceiling: default OFF = $0/query).
2. Provenance always: /ask responses cite only retrieved rows; below-threshold => INSUFFICIENT
   (the verdict taxonomy's own honesty pattern), never a guess.
3. Org-scoping: every product query row-scoped (org_scope pattern); admin-only mutations
   (require_role('admin')); mutations audited.
4. Diff-ability: perspectives are the webhook/notification unit -- snapshot + diff is in v1.1 so
   trust-diff alerts (in-app first, external connectors still parked) attach without rework.
5. Self-tests mirror real variance: heterogeneous labels, None fields, empty corpora -- not
   uniform toys (ops lesson 2026-07-01).

## Sequencing + acceptance bar

Perspectives lane first among equals: it unblocks the tree view and the diff attach point the
webhook roadmap needs. Lane "holds" when: perspective_tree_view renders live facet counts against
prod data, a saved perspective round-trips, and one simulated tier change produces exactly one
notification row. Ask lane holds when: /ask answers a seeded query with correct citations and
returns INSUFFICIENT on an empty corpus. Both lanes green => announce v1.1+v2-preview together.

## Learnings to write back (Graphify KL loop)

Builders/architect: when a build here surfaces a lesson (schema drift, facet that needs a new
column, retrieval quality gap), record it via build_lessons AND a mesh_memory design_note row
tagged perspectives_rag so graph consumers (list_domains/graph_neighbors) can traverse from these
modules to the lesson -- that is the embellishment loop this phase is meant to exercise.
