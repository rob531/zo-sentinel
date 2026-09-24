# Enrichment sources and the preflight gate

Answering "can IPQS be another source for vetting the scored MCPs?"

Short answer: **not as the pipeline is currently keyed** — and the reason
generalises past IPQS, which is why the durable artifact here is the gate rather
than the adapter.

---

## 1. The key is real; the balance is zero

| probe | response |
|---|---|
| deliberately invalid key, `/ip` | `Invalid or unauthorized key.` |
| the `ipqs` secret, `/account` | `You have insufficient credits…` |
| the `ipqs` secret, `/url` | `You have insufficient credits…` |
| the `ipqs` secret, `/ip` | `You have insufficient credits…` |
| the `ipqs` secret, `/email` | `You have insufficient credits…` |

The two messages differ, so this is a balance problem, not an auth problem. The
key is a valid 32-character IPQS key on an account with no credits, across all
three products. IPQS cannot serve a single lookup today.

## 2. Even fully funded, it would buy a constant

`mcp_server_registry` holds 3,189 http URLs. They resolve to **four hostnames**:

| host | servers |
|---|---|
| github.com | 1,611 |
| www.npmjs.com | 1,322 |
| smithery.ai | 254 |
| example.com | 2 |

That column is a *listing page*, not the server's own network identity. A URL
reputation vendor pointed at it is asked the same four questions 3,189 times and
returns the same four answers. The information content is four facts, and the
bill is 3,189 lookups.

This is not hypothetical — it already happened:

```
signal_name = otx_threat_intel     26,017 rows / 1,483 servers
evidence    = {"source":"otx_api","host":"github.com","pulse_count":0,
               "malware_count":0,"reason":"otx_clean"}
```

AlienVault OTX was called 26,017 times to establish that github.com is clean.
`url_safety` is in the same state: 2,467,146 rows over 3,173 servers, **4
distinct scores**, evidence `{"checks": [], "base_score": 70.0}` — a base score
with an empty check list.

Wiring IPQS the same way produces a third correlated copy of the same constant,
at a metered price. The defect is the **join key**, not the vendor.

## 3. Why eight "fix url_safety discrimination" cycles didn't fix it

`directives/done/` contains `url_safety_discrimination_audit`,
`url_safety_weak_signal_fix`, `url_safety_weak_signal_audit`,
`build_url_safety_enrichment`, `url_safety_signal_enrichment` and more — all
marked `.done`, with the signal still uniform.

`url_safety_discrimination_audit.py` cannot detect the condition it was built for:

| line | written | actual |
|---|---|---|
| `WHERE signal_type='url_safety'` | `signal_type` | column is `signal_name` |
| `SELECT score_value` | `score_value` | column is `score` |
| `JOIN … ON s1.mcp_request_id=…` | `mcp_request_id` | no such column |
| `query_result['data']` | `data` | WriteService returns `rows` |

Every one of those queries returns HTTP 400 (measured — binder, parser and
unknown-table errors all do). The audit wraps its request in
`except RequestException: return None`, and `analyze_signal_quality(None)`
returns `{'distinct_scores': 0, 'error': 'No data returned'}`.

**Zero distinct scores reads as "nothing to see" rather than "total
uniformity".** The audit fails open, reports clean, the directive closes, and
the defect survives another cycle. Same family as the fail-open gate in
`smoke_ladder.py`, and the same lesson as cycle-0111: *a verdict must match the
kind of the thing it grades.*

## 4. The cost model nobody was applying

`url_safety` is rewritten for the whole working set roughly hourly — ~1,800 rows
per hour, averaging **778 rows per server**. Measured over the last 24h that is
a 14× daily rescore factor across the 3,189-server population:

```
3,189 servers x 14 rescores/day x 30 days = 1,293,330 lookups/month
```

Against a typical 5,000–25,000 lookup plan that is a 50–250× overrun. A metered
vendor dropped into this loop is exhausted within the hour — which is, for what
it's worth, exactly how the OpenRouter key died earlier in this session.

Nothing in the pipeline was costing an external source before calling it.

---

## What shipped

### `enrichment_preflight.py` — the door

Source-agnostic. Any external enrichment source must arm through it before it is
allowed to write to `mcp_signal_scores`.

| check | blocks when |
|---|---|
| `key_coverage` | <50% of the population resolves to a lookup key |
| `key_cardinality` | <25 distinct keys — the vendor can only return N answers |
| `key_concentration` | one key covers >60% of the population (aggregator) |
| `non_redundancy` | an existing signal is already degenerate on this population |
| `budget` | projected monthly lookups exceed the declared budget, at the cadence **measured off the table** |
| `credits` | the source cannot fund one deduplicated pass |

It **fails closed**. `query()` raises on any non-`rows` response; `assert_columns()`
verifies every referenced column against `information_schema` *before* the query
runs, so a hallucinated column name cannot come back as an absence of findings.
A check that cannot be evaluated is a BLOCK.

`--self-test` measures both poles: BLOCKED on the live registry, ARMED on a
synthetic 199-host population. A gate that only ever blocks is just a different
constant.

### `ipqs_source.py` — the adapter

Complete and wired, and it refuses to run. It deduplicates by key before
spending (4 lookups, not 3,189), inverts IPQS `risk_score` onto the 0–100
"higher is safer" scale with categorical flags clamping rather than nudging, and
**replaces** the day's rows instead of appending — the bug that grew `url_safety`
to 2.4M rows.

```
$ python3 ipqs_source.py --run
ipqs credits: UNUSABLE (0) -- key authenticates but the account balance is zero
preflight[ipqs]: BLOCKED
  [PASS] key_coverage: 100.0% of the population resolves to a key (floor 50%)
  [FAIL] key_cardinality: 4 distinct keys across 3189 rows (floor 25)
  [PASS] key_concentration: top key 'github.com' covers 50.5% (ceiling 60%)
  [FAIL] non_redundancy: url_safety(4 distinct/2467539 rows), domain_age(4/26017)…
  [FAIL] budget: ~95,670 lookups/month at the observed cadence vs budget 5,000
  [FAIL] credits: 0 credits available, 4 needed for one deduplicated pass
Refusing to spend metered lookups.
```

No code change is needed to turn it on. Fix the inputs and it arms itself.

### `test_enrichment_preflight.py`

10 tests, each pinning one way the previous audit failed open — the exact
`signal_type` / `score_value` / `mcp_request_id` / `data` queries included.

---

## Where IPQS would actually earn its keep

It needs a **per-server network identity**. Two populations have one:

1. **Remote/hosted MCP endpoints** — servers reachable on their own domain
   rather than shipped as an npm or GitHub package. Distinct per server. The
   prod MCPRisky registry (`mcplookup` on Fly) is the place to measure this;
   its `/api/servers` needs auth, so that count is still open.
2. **Egress destinations** — the domains a server's code actually contacts,
   extracted during analysis. Distinct per server, and the honest input to the
   `network_egress` signal, which currently has no evidence rows at all.

Point `--population-sql` at either and the gate arms on cardinality.

## Recommended order

1. **Do not buy IPQS credits yet.** They would be spent on a constant.
2. **Fix the rescore loop.** A 778× write amplification is a correctness problem
   before it is a cost problem, and it must be fixed before any metered source
   is wired in, or the budget check is the only thing standing between the
   pipeline and an exhausted key.
3. **Retire or repair `url_safety_discrimination_audit.py`.** It reports clean
   unconditionally. Until it is fixed it is worse than having no audit.
4. **Measure population (1)** — the count of prod servers with their own
   endpoint domain. That number decides whether IPQS is worth funding at all.
5. **Then fund IPQS**, sized to distinct keys rather than server count.

Run `python3 enrichment_preflight.py --self-test` after any change to the
registry or the scoring cadence.
