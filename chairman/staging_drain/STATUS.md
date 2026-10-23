# staging_drain — status (derived; writer: tools/staging_drain/report.py)

Generated 2026-10-04T21:39:11Z from ledger 2026-10-04T21:39:10Z (census head `2879ef79d8fefccf14725d901e8c9f244d61b142`).

```
staging_drain: promoted 0 · superseded 22 · repairing 200 · retired 1259 · remaining 303 · wall: image size UNKNOWN budget (8 promotable api services import to 68 MiB in one process; pass --budget-mb)
```

| outcome | n |
|---|---|
| promoted (live in prod, evidence recorded) | 0 |
| superseded (newer version or an active service owns it) | 22 |
| repairing (open builder directive) | 200 |
| retired (no source; reason recorded; nothing deleted) | 1259 |
| remaining | 303 |
| ↳ promotable api, awaiting S4 (Fly image + boot test + prod drift) | 8 |
| ↳ promotable worker/lib, awaiting S4 (zo scheduled job; zo→prod DB reach unproven) | 13 |
| ↳ repair rows without a directive yet (cap or mechanical) | 282 |
| total staged directories | 1784 |

Repair rows by gate failure class: lib_no_router 155, import_module_not_found 103, contract_missing 90, import_model_name 50, contract_failed 36, import_undefined_name 28, mount_probe_failed 9, test_only_import_at_module_scope 8, import_other 2, hollow_member 1

Promotable now (21; vulnerability families first: ghsa_feed_ingestor, server_cve_search_consumer): app_spine_wiring, ask_corpus_health, axis_entropy_scoring_consumer, axis_score_delta_consumer, axis_score_drift, cadence_job_runs_health_api, family_concentration_scoring_consumer, ghsa_feed_ingestor, known_bad_pattern_enrichment_v3, perspective_event_management, registry_source_freshness_metrics, risk_tier_assign, risk_tier_statistics, risk_tier_watchlist, risk_tier_writer, score_to_tier_consumer, server_cve_search_consumer, server_registry_csv_export, threat_intel_summary, unit_promotion_readiness_report, wire_routers_into_main_app

Wall: {"status": "measured", "services": 8, "final_rss_kb": 70648, "budget_mb": null}
