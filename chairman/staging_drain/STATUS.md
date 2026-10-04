# staging_drain — status (derived; writer: tools/staging_drain/report.py)

Generated 2026-10-04T21:15:14Z from ledger 2026-10-04T21:15:13Z (census head `6b3abd0accb1cfbfcae7e5780ebf0ee49c59c146`).

```
staging_drain: promoted 0 · superseded 22 · repairing 100 · retired 1259 · remaining 403 · wall: image size UNKNOWN budget (25 promotable api services import to 70 MiB in one process; pass --budget-mb)
```

| outcome | n |
|---|---|
| promoted (live in prod, evidence recorded) | 0 |
| superseded (newer version or an active service owns it) | 22 |
| repairing (open builder directive) | 100 |
| retired (no source; reason recorded; nothing deleted) | 1259 |
| remaining | 403 |
| ↳ promotable api, awaiting S4 (Fly image + boot test + prod drift) | 25 |
| ↳ promotable worker/lib, awaiting S4 (zo scheduled job; zo→prod DB reach unproven) | 13 |
| ↳ repair rows without a directive yet (cap or mechanical) | 365 |
| total staged directories | 1784 |

Repair rows by gate failure class: lib_no_router 155, import_module_not_found 103, contract_missing 90, import_model_name 50, contract_failed 36, import_undefined_name 28, import_other 2, hollow_member 1

Promotable now (38; vulnerability families first: ghsa_feed_ingestor, server_cve_exposure_api, server_cve_search_consumer): app_spine_wiring, ask_corpus_drift, ask_corpus_health, axis_entropy_scoring_consumer, axis_score_delta_consumer, axis_score_drift, cadence_job_health_consumer, cadence_job_runs_health_api, cadence_monitor, circuit_breaker_state_api, directive_queue_health, directive_queue_health_api, directive_queue_starvation_timeline, family_concentration_scoring_consumer, ghsa_feed_ingestor, known_bad_pattern_enrichment_v3, perspective_event_management, perspective_snapshot_rollup, registry_source_freshness_metrics, risk_summary_external_api, risk_tier_assign, risk_tier_distribution_api, risk_tier_statistics, risk_tier_transition_alerts, risk_tier_watchlist, risk_tier_writer, run_reconciliation_report, score_to_tier_consumer, server_cve_exposure_api, server_cve_search_consumer, server_freshness_monitor, server_registry_csv_export, server_risk_tier_distribution_dashboard, server_risk_tier_snapshot_api, threat_intel_summary, unit_atomicity_gate, unit_promotion_readiness_report, wire_routers_into_main_app

Wall: {"status": "measured", "services": 25, "final_rss_kb": 72620, "budget_mb": null}
