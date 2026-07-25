# Orphan review packet — for Fable 5

**Ask:** the SOA fail-loud spine + reachability census surfaced **317 orphan modules** (root routers mounted nowhere). For each, rule **KEEP** (load-bearing / necessary feature → mount it), **REVIVE** (dead only on the model-casing bug → `model_import_linter --fix` then judge), or **REMIT** (retire the originating directive). This packet + `orphan_review.csv` (all 317, one row each, with provenance) + `orphanage/manifest.json` (raw) are the inputs.

## Summary

| class | count | meaning |
|---|---|---|
| **BROKEN_IMPORT** | 159 | dead on model-name casing: model_import_linter --fix likely revives |
| **MOUNTABLE** | 140 | clean + data-wired: promotion candidate (keep, mount) |
| **UNKNOWN** | 14 | needs human/Fable-5 judgement |
| **NO_ROUTES** | 3 | declares no routes: probably not a service: remit candidate |
| **EDIT_CLASS** | 1 | wire_/integrate_ name: structurally unbuildable, never a service: remit |

**Headline:** only **4** are clear remits; ~**299** are salvageable (**159** revived by one linter pass, **140** clean & mountable). The graveyard is mostly ladder-built features killed by a typo or by having had no spine to mount them — NOT junk. The real judgement is the 140 MOUNTABLE (which are load-bearing vs near-dups) and the 14 UNKNOWN.

## REMIT candidates (retire the directive) — 4

### REMIT  (4)

| module | routes | data-layer | lines | origin | date |
|---|---|---|---|---|---|
| `wire_orphan_value_routers` | 1 | yes | 85 | build: build_wire_orphan_value_routers (#161 | 2026-07-18 |
| `audit_log_api` | 0 | yes | 268 | build: product_audit_log (#1763) | 2026-07-23 |
| `sentinel_external_api_v2_history` | 0 | no | 124 | ZoSentinel v2.0 live codebase 2026-05-25 | 2026-05-25 |
| `verify_github_pr_checker_webhook_wiring` | 0 | no | 599 | ZoSentinel v2.0 live codebase 2026-05-25 | 2026-05-25 |

## UNKNOWN — need judgement — 14

### UNKNOWN  (14)

| module | routes | data-layer | lines | origin | date |
|---|---|---|---|---|---|
| `admin_policies_integration` | 8 | no | 274 | build: admin_policies_integration (#483) | 2026-06-23 |
| `admin_exemptions_integration` | 7 | no | 328 | build: admin_exemptions_integration (#469) | 2026-06-23 |
| `mcp_decisions_api` | 5 | no | 128 | build: build_mcp_decisions_management_api (# | 2026-06-25 |
| `sentinel_external_api_v2_signals` | 2 | no | 301 | ZoSentinel v2.0 live codebase 2026-05-25 | 2026-05-25 |
| `sentinel_ui_evidence_drawer_route` | 2 | no | 72 | ZoSentinel v2.0 live codebase 2026-05-25 | 2026-05-25 |
| `sentinel_ui_signal_diversity_panel` | 2 | no | 102 | ZoSentinel v2.0 live codebase 2026-05-25 | 2026-05-25 |
| `compliance_overview_api` | 1 | no | 155 | build: build_compliance_overview_api (#615) | 2026-06-25 |
| `mcp_definition_history_report_api` | 1 | no | 225 | build: build_mcp_definition_history_report_a | 2026-06-25 |
| `mcp_search_autocomplete_api` | 1 | no | 74 | build: build_mcp_search_autocomplete_api (#5 | 2026-06-25 |
| `risk_trend_api` | 1 | no | 208 | build: build_risk_trend_api (#613) | 2026-06-25 |
| `sentinel_external_api_v2_attestation` | 1 | no | 135 | ZoSentinel v2.0 live codebase 2026-05-25 | 2026-05-25 |
| `sentinel_ui_inventory_paginator` | 1 | no | 241 | ZoSentinel v2.0 live codebase 2026-05-25 | 2026-05-25 |
| `system_health_dashboard_api` | 1 | no | 85 | build: build_system_health_dashboard_api (#6 | 2026-06-25 |
| `wiring_snow_connector_to_approval` | 1 | no | 51 | ZoSentinel v2.0 live codebase 2026-05-25 | 2026-05-25 |

## MOUNTABLE — the load-bearing-vs-dup call — 140 (top 40 by route_count; full set in CSV)

### MOUNTABLE  (140)

| module | routes | data-layer | lines | origin | date |
|---|---|---|---|---|---|
| `perspective_service` | 5 | yes | 274 | build: perspective_service (#1500) | 2026-07-15 |
| `server_policy_rules_api` | 5 | yes | 227 | build: server_policy_rules_api (#1374) | 2026-07-10 |
| `cadence_job_runs_api` | 3 | yes | 219 | build: cadence_job_runs_api (#1381) | 2026-07-10 |
| `mcp_score_dispute_api` | 3 | yes | 301 | build: mcp_score_dispute_api (#1319) | 2026-07-07 |
| `api_key_rotation_service` | 2 | yes | 216 | build: build_api_key_rotation_service (#1546 | 2026-07-16 |
| `cadence_pipeline_health_report` | 2 | yes | 221 | build: cadence_pipeline_health_report (#1515 | 2026-07-15 |
| `mcp_signal_evidence_archive_api` | 2 | yes | 150 | build: build_mcp_signal_evidence_archive_api | 2026-07-07 |
| `rbac_enforcer` | 2 | yes | 162 | build: build_rbac_enforcer (#1049) | 2026-06-29 |
| `risk_tier_alert_router` | 2 | yes | 108 | build: build_risk_tier_alert_router (#1775) | 2026-07-24 |
| `scoring_wave_cost_ledger_api` | 2 | yes | 169 | build: scoring_wave_cost_ledger_api (#1568) | 2026-07-17 |
| `scoring_wave_detail_api` | 2 | yes | 216 | build: scoring_wave_detail_api (#1610) | 2026-07-18 |
| `sentinel_service_health_api` | 2 | yes | 146 | build: sentinel_service_health_api (#1444) | 2026-07-13 |
| `server_exemption_audit_trail_api` | 2 | yes | 397 | build: server_exemption_audit_trail_api (#13 | 2026-07-10 |
| `server_exemptions_history_api` | 2 | yes | 159 | build: server_exemptions_history_api (#1385) | 2026-07-10 |
| `server_submission_api` | 2 | yes | 101 | build: server_submission_api (#1757) | 2026-07-23 |
| `server_verdict_timeline_api` | 2 | yes | 165 | build: server_verdict_timeline_api (#1326) | 2026-07-07 |
| `server_vuln_severity_distribution_api` | 2 | yes | 121 | build: server_vuln_severity_distribution_api | 2026-07-16 |
| `server_vuln_summary_api` | 2 | yes | 301 | build: server_vuln_summary_api (#1364) | 2026-07-09 |
| `service_health_aggregator_api` | 2 | yes | 138 | build: service_health_aggregator_api (#1352) | 2026-07-09 |
| `ask_answer_export_service` | 1 | yes | 220 | build: ask_answer_export_service (#1550) | 2026-07-16 |
| `ask_corpus_search_api` | 1 | yes | 120 | build: ask_corpus_search_api (#1440) | 2026-07-12 |
| `ask_corpus_timeline_api` | 1 | yes | 80 | build: ask_corpus_timeline_api (#1423) | 2026-07-11 |
| `ask_query_expansion_api_v3` | 1 | yes | 66 | build: build_ask_query_expansion_api_v3 (#14 | 2026-07-13 |
| `ask_query_expansion_v2` | 1 | yes | 107 | build: build_ask_query_expansion_v2 (#1310) | 2026-07-06 |
| `audit_log_export_api` | 1 | yes | 159 | build: audit_log_export_api (#1731) | 2026-07-22 |
| `axis_change_attribution_probe` | 1 | yes | 309 | build: build_axis_change_attribution_probe ( | 2026-07-21 |
| `axis_distribution_api` | 1 | yes | 90 | build: build_axis_distribution_api_v1 (#1007 | 2026-06-28 |
| `axis_p_top_calibration_api` | 1 | yes | 293 | build: build_axis_p_top_calibration_api (#16 | 2026-07-20 |
| `axis_top_labels_api` | 1 | yes | 163 | build: axis_top_labels_api (#1548) | 2026-07-16 |
| `cadence_job_runs_recent_api` | 1 | yes | 125 | build: cadence_job_runs_recent_api (#1406) | 2026-07-10 |
| `circuit_breaker_status_api` | 1 | yes | 115 | build: circuit_breaker_status_api (#1695) | 2026-07-21 |
| `cve_facet_compile_wiring_v3` | 1 | yes | 79 | build: build_cve_facet_compile_wiring_v3 (#1 | 2026-07-22 |
| `deferred_router_ledger_report` | 1 | yes | 61 | build: build_deferred_router_ledger_report ( | 2026-07-21 |
| `escalation_timeline_api` | 1 | yes | 189 | build: escalation_timeline_api (#1392) | 2026-07-10 |
| `fleet_exploit_surface_api` | 1 | yes | 163 | build: fleet_exploit_surface_api (#1441) | 2026-07-12 |
| `fleet_risk_composition_api` | 1 | yes | 117 | build: fleet_risk_composition_api (#1430) | 2026-07-12 |
| `gate_attribution_report_api` | 1 | yes | 95 | build: build_gate_attribution_report_api (#1 | 2026-07-20 |
| `gate_attribution_report_router` | 1 | yes | 164 | build: gate_attribution_report_router (#1693 | 2026-07-21 |
| `ghost_retry_burn_report` | 1 | yes | 130 | build: ghost_retry_burn_report (#1681) | 2026-07-20 |
| `harvest_lane_throughput_report` | 1 | yes | 149 | build: harvest_lane_throughput_report (#1570 | 2026-07-17 |
| … +100 more in orphan_review.csv | | | | | |

## BROKEN_IMPORT — casing-revivable — 159 (top 20; full set in CSV)

### BROKEN_IMPORT  (159)

| module | routes | data-layer | lines | origin | date |
|---|---|---|---|---|---|
| `sentinel_external_api_router` | 5 | yes | 223 | build: build_sentinel_external_api_router (# | 2026-07-23 |
| `server_exemptions_api` | 4 | yes | 186 | build: server_exemptions_api (#1376) | 2026-07-10 |
| `axis_scores_query_api` | 3 | yes | 307 | build: axis_scores_query_api (#1770) | 2026-07-24 |
| `risk_tier_axis_score_consumer_router` | 3 | yes | 223 | build: build_risk_tier_axis_score_consumer_r | 2026-07-20 |
| `scoring_precision_audit_report` | 3 | yes | 237 | build: scoring_precision_audit_report (#1535 | 2026-07-16 |
| `server_risk_tier_bulk_lookup_api` | 3 | yes | 156 | build: server_risk_tier_bulk_lookup_api (#14 | 2026-07-10 |
| `server_vuln_advisories_api` | 3 | yes | 270 | build: server_vuln_advisories_api (#1363) | 2026-07-09 |
| `dispute_summary_api` | 2 | yes | 89 | build: dispute_summary_api (#1499) | 2026-07-15 |
| `entity_report_exporter_router` | 2 | yes | 119 | build: entity_report_exporter_router (#1651) | 2026-07-19 |
| `fleet_risk_tier_aggregation_api` | 2 | yes | 202 | build: fleet_risk_tier_aggregation_api (#139 | 2026-07-10 |
| `risk_tier_criteria_api` | 2 | yes | 120 | build: risk_tier_criteria_api (#1328) | 2026-07-07 |
| `scoring_consistency_audit_api` | 2 | yes | 217 | build: scoring_consistency_audit_api (#1740) | 2026-07-22 |
| `scoring_coverage_audit_api` | 2 | yes | 131 | build: scoring_coverage_audit_api (#1554) | 2026-07-16 |
| `scoring_gap_analysis_api` | 2 | yes | 157 | build: scoring_gap_analysis_api (#1464) | 2026-07-14 |
| `server_freshness_dashboard_api` | 2 | yes | 137 | build: server_freshness_dashboard_api (#1434 | 2026-07-12 |
| `server_risk_tier_computation_api` | 2 | yes | 190 | build: server_risk_tier_computation_api (#13 | 2026-07-07 |
| `server_scoring_status_api` | 2 | yes | 158 | build: build_server_scoring_status_api (#175 | 2026-07-23 |
| `server_verdict_scoring_api` | 2 | yes | 248 | build: server_verdict_scoring_api (#1330) | 2026-07-07 |
| `verdict_changes_api` | 2 | yes | 195 | build: verdict_changes_api (#1318) | 2026-07-07 |
| `verdict_dashboard_api` | 2 | no | 145 | build: build_verdict_dashboard_api (#587) | 2026-06-25 |
| … +139 more in orphan_review.csv | | | | | |

---
*Generated by tools/orphanage.py from the reachability census + git provenance. Read-only; nothing mounted, remitted, or revived without the chairman's call.*