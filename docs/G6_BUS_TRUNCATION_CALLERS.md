# G6 — callers reading a partial database through `:8772/query`

Companion to `MERGE_AUDIT_2026-08-23.md` finding **G6**. Two halves: the fix to
the endpoint, and the census of callers that have been reasoning about a
truncated result set without knowing it.

## The mechanism

`write_service._query_via_writer_locked` appends `LIMIT 200` to any SQL that does
not already contain the word `limit`, then returns

```json
{"rows": [...], "count": <rows RETURNED>}
```

`count` is the size of the truncated page, not the number of rows that matched,
and nothing in the response distinguishes a complete answer from a capped one.
A caller that does not paginate cannot tell the difference.

Confirmed live — the engine echoes the injected clause back in an error:

```
LINE 1: SELECT COUNT(*) FROM server_scores LIMIT 200
```

## The fix

`ops/zo_mesh/write_service_g6_truncated_flag.patch` adds an explicit flag:

```json
{"rows": [...], "count": 200, "truncated": true, "limit": 200,
 "hint": "result truncated at the server-injected LIMIT 200; re-query with an explicit LIMIT/OFFSET to page"}
```

`truncated` is true only when the cap **this service injected** actually filled
up. A caller's own `LIMIT` is that caller's choice, not a silent truncation, and
an injected cap that came back short truncated nothing.

**Not yet applied.** `write_service.py` lives at `/home/workspace/zo_mesh/`,
which is a local-only git repository with no remote, so the change cannot be
carried by a pull request to this repo. It also requires a restart of a live
service to take effect. Both are operator decisions — see the remediation
section of the audit.

Behaviour of the patch, verified by extracting the shipped method and running it
against a stub connection (no `duckdb.connect()`, and no import of
`write_service.py`, whose module-level code would open the live database):

| rows available | as deployed | patched |
|---|---|---|
| 9,611,610 | `count=200`, no flag | `count=200`, **`truncated=true`** |
| 200 (exactly the cap) | `count=200`, no flag | `count=200`, **`truncated=true`** |
| 199 | `count=199`, no flag | `count=199`, `truncated=false` |
| 3 | `count=3`, no flag | `count=3`, `truncated=false` |
| 0 | `count=0`, no flag | `count=0`, `truncated=false` |
| 9,611,610, caller wrote `LIMIT 50` | `count=50`, no flag | `count=50`, `truncated=false` |

Exactly-200 reports `truncated: true`. The server cannot distinguish "exactly
200 matched" from "more matched", and over-reporting a possible truncation is
the safe direction.

## The census

Method: every tracked `.py` that talks to `:8772/query`, AST-extract its SQL
string literals, and keep those that are row-returning, carry no `LIMIT` of their
own, and sit in a file that does not paginate with `OFFSET`. Aggregate-only
projections (`COUNT`, `MAX`, …) are excluded — a row cap cannot truncate a single
scalar. Table row counts are live, read from the bus (paginated).

**16 of the 44 tables on the bus currently hold more than 200 rows.**

### A. `information_schema` enumerations with no `table_name` narrowing

The case this audit hit itself: 200 rows / 25 tables reported when the truth was
355 columns / 44 tables. **17 sites in 13 files.**

The two that matter most are instruments, not scripts:

| Site | What it enumerates |
|---|---|
| `tests/ci/smoke_ladder.py:261` | `SELECT table_name FROM information_schema.tables WHERE table_schema='main'` |
| `tests/gates/gate_framework.py:214` | `SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'` |

Others: `daily_check.py:276`, `integration_test.py:89`, `refresh_schema_doc.py:85`,
`tests/gate_errors_bootstrap.py:323`, `tests/integration/it_write_service.py:84`,
`snow_connector_wiring_check.py:119`, `snow_integration_final_verify.py:125`,
`verify_snow_connector_inbound_wiring.py:137,139,141,176`,
`verify_snow_connector_integration_v2.py:162`.

With 44 tables the cap does not bite *today*, so these are latent rather than
currently wrong — but `refresh_schema_doc.py` enumerates
`information_schema.columns`, and there are 355 columns, which is already over
the cap. The schema doc it generates is therefore built from a partial read.

### B. Unbounded row reads against tables that exceed the cap today

**701 sites in 366 files across 11 tables.** These are provably partial: the
caller receives 200 rows and is told `count: 200`.

| Table | Rows | Sites | Files |
|---|---:|---:|---:|
| `mcp_signal_scores` | 9,611,610 | 283 | 215 |
| `code_edges` | 428,949 | 5 | 3 |
| `code_nodes` | 338,660 | 2 | 2 |
| `write_queue_log` | 162,570 | 1 | 1 |
| `mesh_memory` | 120,403 | 141 | 118 |
| `mesh_events` | 46,776 | 4 | 4 |
| `audit_log` | 28,402 | 28 | 21 |
| `mcp_llm_axis_scores` | 13,267 | 11 | 11 |
| `build_provenance` | 11,559 | 2 | 1 |
| `mcp_fingerprints` | 3,275 | 6 | 6 |
| `mcp_server_registry` | 3,203 | 218 | 151 |

Most of the 366 files are one-off diagnostic scripts whose blast radius is a
wrong number in a report nobody reads twice. A few are not, and those are the
ones worth naming.

### The graph readers — the highest-consequence subset

`code_nodes` (338,660) and `code_edges` (428,949) are the graphify graph. Every
site below receives **at most 200 edges** and is told that is the whole answer:

| Site | Reads |
|---|---|
| `goose_runner.py:453` | `code_edges` — builds the builder's GRAPH CONTEXT block |
| `mcp_servers/builder_mcp.py:175,180` | `code_edges` — caller/callee neighbourhoods |
| `zo_sentinel/mcp_servers/directive_mcp.py:511` | `code_nodes` — existence probe |
| `zo_sentinel/mcp_servers/directive_mcp.py:545,550` | `code_edges` — dependants/dependencies |
| `tools/fu/fu_context.py:201` | `code_nodes` joined to `code_edges` |

`goose_runner.py:453` is the line that produces the
`GRAPH CONTEXT -- existing code that depends on <file> (do NOT break these
contracts)` block handed to every build directive. That list is capped at 200
edges with no indication, so a directive can be told it has the complete set of
dependants when it does not. This bears directly on G4(c): the graph is the
obvious candidate for answering "does a mount point exist for this?" at emission
time, and it is currently read through a silent cap.

## Recommended order

1. Apply the patch and restart `write_service` — until then no caller *can*
   detect truncation.
2. Fix the graph readers, which are few (5 sites, 3 files) and the most
   consequential.
3. Fix `refresh_schema_doc.py`, which is already over the cap on
   `information_schema.columns`.
4. Leave the long tail of diagnostic scripts; a `truncated` flag in the response
   at least makes a wrong answer detectable when one is next read.
