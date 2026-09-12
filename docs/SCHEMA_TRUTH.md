# SCHEMA TRUTH -- the exact names that exist in `app/`

GENERATED FILE -- do not hand-edit. Regenerate with `python tools/emit_schema_truth.py`.
`tests/test_schema_truth_current.py` (in the evaluator allowlist) re-runs the emitter
against the current `app/models.py` and `app/db.py` and fails if this file has drifted,
so it cannot silently go stale.

This file is DESCRIPTIVE. It reports what exists; it does not propose what should.

## Copy these import lines verbatim

```python
from app.db import get_session                  # the one session dependency
from app.models import <Model>                  # <Model> MUST be one of the 14 below
```

Test-time dependency override -- the only correct spelling:

```python
from app.main import app                        # `app` here is the FastAPI INSTANCE
from sqlalchemy.pool import StaticPool          # NOT from app.db
app.dependency_overrides[get_session] = _override
```

## Does not exist (measured from 2026-08-09 self-test REDs)

| written by the builder | REDs that day | what to write instead |
| --- | --- | --- |
| `app.dependency_overrides` | 27 | `app` is the PACKAGE app/. The FastAPI instance is app.main:app -- write `from app.main import app`, then `app.dependency_overrides[get_session] = ...` |
| `from app.db import StaticPool` | 15 | StaticPool is a SQLAlchemy pool class: `from sqlalchemy.pool import StaticPool` |
| `from app.models import MCPAxisScores` | 7 | the real class is `McpLlmAxisScore` (table mcp_llm_axis_scores) |
| `from app.models import VulnAdvisories` | 6 | the real class is `VulnAdvisory` (singular; table vuln_advisories) |

If the model you want is not in the table below, it DOES NOT EXIST. Use the closest
real class, or say in the build notes that the directive needs a schema decision --
do not invent a class name.

### the dominant failure is SPELLING, not absence

Measured 2026-08-09 over the 2367 tracked .py files: **109 distinct names** are imported
from `app.models` that do not exist, spread over **370 modules**. The four commonest are
case/plural variants of classes that DO exist:

| written | files | real spelling |
| --- | --- | --- |
| `MCPServerRegistry` | 158 | `McpServerRegistry` |
| `MCPLLMAxisScores` | 112 | `McpLlmAxisScore` |
| `McpLlmAxisScores` | 33 | `McpLlmAxisScore` |
| `MCPScoreDisputes` | 31 | `McpScoreDispute` |

Python matches class names character for character: `MCP...` is never right, and no class
is plural. Copy the spelling out of the table below rather than reconstructing it.

## `app.models` -- all 14 classes, exhaustive

| class | `__tablename__` | columns |
| --- | --- | --- |
| `Org` | `orgs` | 3 |
| `User` | `users` | 9 |
| `ApiKey` | `api_keys` | 5 |
| `McpServerRegistry` | `mcp_server_registry` | 16 |
| `McpLlmAxisScore` | `mcp_llm_axis_scores` | 15 |
| `McpScoreDispute` | `mcp_score_disputes` | 11 |
| `Perspective` | `perspectives` | 8 |
| `PerspectiveSnapshot` | `perspective_snapshots` | 4 |
| `PerspectiveEvent` | `perspective_events` | 8 |
| `AskCorpusDoc` | `ask_corpus_index` | 5 |
| `VulnAdvisory` | `vuln_advisories` | 13 |
| `VulnLink` | `vuln_links` | 7 |
| `ThreatIntelRef` | `threat_intel_refs` | 10 |
| `CadenceJobRun` | `cadence_job_runs` | 7 |

### columns per class

- `Org` (`orgs`): `id`, `name`, `created_at`
- `User` (`users`): `id`, `email`, `password_hash`, `org_id`, `role`, `created_at`, `clerk_id`, `clerk_synced_via`, `clerk_created_at`
- `ApiKey` (`api_keys`): `id`, `org_id`, `key_hash`, `label`, `created_at`
- `McpServerRegistry` (`mcp_server_registry`): `server_id`, `name`, `registry_source`, `url`, `description`, `trust_score`, `verdict`, `verdict_reasoning`, `confidence`, `last_assessed`, `first_seen`, `last_seen`, `last_scanned`, `scan_count`, `risk_tier`, `meta`
- `McpLlmAxisScore` (`mcp_llm_axis_scores`): `id`, `server_id`, `axis_name`, `label`, `label_index`, `probs`, `p_top`, `p_critical`, `p_danger`, `escalated`, `escalated_to`, `decision_rule_version`, `model_version`, `adapter_sha256`, `scored_at`
- `McpScoreDispute` (`mcp_score_disputes`): `id`, `server_id`, `submitted_by`, `proposed_overall_risk`, `proposed_axes`, `reason_category`, `explanation`, `status`, `admin_note`, `created_at`, `resolved_at`
- `Perspective` (`perspectives`): `id`, `org_id`, `name`, `description`, `facet_filters`, `created_by`, `created_at`, `updated_at`
- `PerspectiveSnapshot` (`perspective_snapshots`): `id`, `perspective_id`, `taken_at`, `membership`
- `PerspectiveEvent` (`perspective_events`): `id`, `perspective_id`, `server_id`, `change_type`, `old_tier`, `new_tier`, `seen`, `created_at`
- `AskCorpusDoc` (`ask_corpus_index`): `server_id`, `snippet`, `terms`, `content_hash`, `indexed_at`
- `VulnAdvisory` (`vuln_advisories`): `id`, `feed`, `summary`, `severity`, `ecosystem`, `package`, `affected_ranges`, `aliases`, `source_url`, `published_at`, `fetched_at`, `identities`, `content_hash`
- `VulnLink` (`vuln_links`): `id`, `advisory_id`, `server_id`, `match_basis`, `match_value`, `match_confidence`, `linked_at`
- `ThreatIntelRef` (`threat_intel_refs`): `id`, `indicator_type`, `indicator_value`, `pulse_id`, `pulse_name`, `pulse_created`, `is_aggregator`, `source`, `source_url`, `fetched_at`
- `CadenceJobRun` (`cadence_job_runs`): `id`, `job`, `status`, `started_at`, `finished_at`, `rows_affected`, `detail`

## `app.models` -- all public top-level names, exhaustive

`ApiKey`, `AskCorpusDoc`, `Base`, `BigInteger`, `Boolean`, `CadenceJobRun`, `DateTime`, `Float`, `ForeignKey`, `Integer`, `JSON`, `Mapped`, `McpLlmAxisScore`, `McpScoreDispute`, `McpServerRegistry`, `Optional`, `Org`, `Perspective`, `PerspectiveEvent`, `PerspectiveSnapshot`, `String`, `Text`, `ThreatIntelRef`, `UniqueConstraint`, `User`, `VulnAdvisory`, `VulnLink`, `datetime`, `func`, `mapped_column`

(That list includes third-party names re-exported by the module -- `Base` is genuinely
importable from `app.models`, but prefer importing SQLAlchemy names from SQLAlchemy.)

## `app.db` -- all public top-level names, exhaustive

`Base`, `DATABASE_URL`, `Session`, `SessionLocal`, `create_engine`, `declarative_base`, `engine`, `get_session`, `init_db`, `sessionmaker`, `settings`

Anything not in that list is not importable from `app.db`.
