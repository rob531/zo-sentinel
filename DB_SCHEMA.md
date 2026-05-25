# ZO-SENTINEL DuckDB Schema
# AUTO-GENERATED 2026-05-25 22:38 UTC
# Regenerate: python3 /home/workspace/zo_sentinel/refresh_schema_doc.py

## audit_log
| Column | Type |
|--------|------|
| event_id | VARCHAR |
| event_type | VARCHAR |
| actor | VARCHAR |
| target_server_id | VARCHAR |
| action | VARCHAR |
| outcome | VARCHAR |
| details_json | VARCHAR |
| immutable | BOOLEAN |
| timestamp | TIMESTAMPTZ |

## auth_tokens
| Column | Type |
|--------|------|
| token_id | VARCHAR |
| action | VARCHAR |
| mcp_name | VARCHAR |
| submission_id | VARCHAR |
| requested_by | VARCHAR |
| admin_email | VARCHAR |
| expires_at | TIMESTAMPTZ |
| used | BOOLEAN |
| used_at | TIMESTAMPTZ |
| created_at | TIMESTAMPTZ |

## build_provenance
| Column | Type |
|--------|------|
| build_id | VARCHAR |
| directive_id | VARCHAR |
| directive_type | VARCHAR |
| complexity | VARCHAR |
| engine | VARCHAR |
| model | VARCHAR |
| backend | VARCHAR |
| smoke_result | VARCHAR |
| rescue_count | INTEGER |
| success | BOOLEAN |
| output_path | VARCHAR |
| output_bytes | INTEGER |
| error | VARCHAR |
| built_at | TIMESTAMPTZ |

## github_velocity
| Column | Type |
|--------|------|
| server_id | VARCHAR |
| repo_url | VARCHAR |
| commit_velocity | FLOAT |
| contributor_churn | FLOAT |
| last_suspicious_commit | VARCHAR |
| checked_at | TIMESTAMPTZ |

## mcp_attestations
| Column | Type |
|--------|------|
| attestation_id | VARCHAR |
| server_id | VARCHAR |
| attestation_text | VARCHAR |
| scope | VARCHAR |
| confidence_level | VARCHAR |
| valid_until | TIMESTAMPTZ |
| risk_tier | VARCHAR |
| caveats | VARCHAR |
| status | VARCHAR |
| generated_at | TIMESTAMPTZ |

## mcp_decisions
| Column | Type |
|--------|------|
| id | BIGINT |
| submission_id | VARCHAR |
| analyst_name | VARCHAR |
| decision | VARCHAR |
| conditions | VARCHAR |
| notes | VARCHAR |
| expiry_days | INTEGER |
| expires_at | TIMESTAMPTZ |
| decided_at | TIMESTAMPTZ |

## mcp_definition_history
| Column | Type |
|--------|------|
| id | BIGINT |
| server_id | VARCHAR |
| snapshot_hash | VARCHAR |
| snapshot_content | VARCHAR |
| captured_at | TIMESTAMPTZ |

## mcp_directory_mentions
| Column | Type |
|--------|------|
| id | BIGINT |
| server_id | VARCHAR |
| directory_name | VARCHAR |
| mention_name | VARCHAR |
| mention_url | VARCHAR |
| mention_context | VARCHAR |
| mention_status | VARCHAR |
| mention_rank | INTEGER |
| first_seen | TIMESTAMPTZ |
| last_seen | TIMESTAMPTZ |

## mcp_discovery_candidates
| Column | Type |
|--------|------|
| id | BIGINT |
| candidate_name | VARCHAR |
| candidate_url | VARCHAR |
| candidate_description | VARCHAR |
| discovered_in_directory | VARCHAR |
| discovered_status | VARCHAR |
| first_seen | TIMESTAMPTZ |
| last_seen | TIMESTAMPTZ |
| reviewed_at | TIMESTAMPTZ |
| promoted | BOOLEAN |

## mcp_ecosystems_metadata
| Column | Type |
|--------|------|
| id | BIGINT |
| server_id | VARCHAR |
| top_package_name | VARCHAR |
| top_package_purl | VARCHAR |
| top_ecosystem | VARCHAR |
| top_downloads | BIGINT |
| top_latest_version | VARCHAR |
| cousin_count | INTEGER |
| ecosystems_observed | VARCHAR |
| age_days_estimate | INTEGER |
| stars_estimate | INTEGER |
| raw_response_bytes | INTEGER |
| lookup_status | VARCHAR |
| last_error | VARCHAR |
| fetched_at | TIMESTAMPTZ |

## mcp_exemptions
| Column | Type |
|--------|------|
| exemption_id | VARCHAR |
| server_id | VARCHAR |
| reason | VARCHAR |
| granted_by | VARCHAR |
| conditions_json | VARCHAR |
| expires_at | TIMESTAMPTZ |
| active | BOOLEAN |
| created_at | TIMESTAMPTZ |

## mcp_fingerprints
| Column | Type |
|--------|------|
| server_id | VARCHAR |
| tool_name_hash | VARCHAR |
| description_tokens | VARCHAR |
| permission_scope_hash | VARCHAR |
| domain_fingerprint | VARCHAR |
| version_string | VARCHAR |
| computed_at | TIMESTAMPTZ |

## mcp_policy_rules
| Column | Type |
|--------|------|
| id | BIGINT |
| rule_name | VARCHAR |
| rule_type | VARCHAR |
| pattern | VARCHAR |
| action | VARCHAR |
| description | VARCHAR |
| created_at | TIMESTAMPTZ |

## mcp_registry_facts
| Column | Type |
|--------|------|
| id | BIGINT |
| registry_name | VARCHAR |
| version | VARCHAR |
| description | VARCHAR |
| status | VARCHAR |
| published_at | TIMESTAMPTZ |
| is_latest | BOOLEAN |
| package_count | INTEGER |
| primary_registry | VARCHAR |
| primary_identifier | VARCHAR |
| raw_packages | VARCHAR |
| server_id | VARCHAR |
| first_seen | TIMESTAMPTZ |
| last_seen | TIMESTAMPTZ |

## mcp_risk_register
| Column | Type |
|--------|------|
| server_id | VARCHAR |
| name | VARCHAR |
| risk_rank | FLOAT |
| risk_tier | VARCHAR |
| threat_count | INTEGER |
| staleness_days | INTEGER |
| computed_at | TIMESTAMPTZ |

## mcp_server_registry
| Column | Type |
|--------|------|
| server_id | VARCHAR |
| name | VARCHAR |
| registry_source | VARCHAR |
| url | VARCHAR |
| description | VARCHAR |
| trust_score | FLOAT |
| verdict | VARCHAR |
| verdict_reasoning | VARCHAR |
| confidence | FLOAT |
| last_assessed | TIMESTAMPTZ |
| first_seen | TIMESTAMPTZ |
| last_seen | TIMESTAMPTZ |
| last_scanned | TIMESTAMPTZ |
| scan_count | INTEGER |
| risk_tier | VARCHAR |
| metadata | VARCHAR |

## mcp_signal_enrichments
| Column | Type |
|--------|------|
| id | BIGINT |
| run_id | VARCHAR |

## Common Mistakes
- audit_log: `timestamp` not `created_at`; `target_server_id` not `server_id`
- mcp_submissions: `requested_by` not `requester_name`; `mcp_name` not `mcp_identifier`
- mcp_risk_register: `computed_at` not `last_assessed`
- mcp_policy_rules: use `rule_type`+`pattern`, no condition_field/condition_operator
- service_health: has `status` and `meta` columns
