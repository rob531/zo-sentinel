from app.router_verdict import router as verdict_router
from app.router_registry import router as registry_router
from app.router_verdict_breakdown_api import router as verdict_breakdown_router
from app.router_verdict_axis_detail_api import router as verdict_axis_detail_router
from app.router_server_composite_risk_ranking_api import router as server_composite_risk_ranking_router
from app.router_dispute_detail_api import router as dispute_detail_router
from app.router_risk_tier_threshold_api import router as risk_tier_threshold_router
from app.router_audit_log_query_api import router as audit_log_query_router
from app.router_cve_facet_compile_api import router as cve_facet_compile_router
from app.router_verdict_export_api import router as verdict_export_router
from app.router_server_risk_tier_export_api import router as server_risk_tier_export_router
from app.router_server_scorecard_api import router as server_scorecard_router
from app.router_overview_dashboard_api import router as overview_dashboard_router
from app.router_dashboard_summary_api import router as dashboard_summary_router
from app.router_verdict_view_api import router as verdict_view_router
from app.router_org_entity_search_api import router as org_entity_search_router
from app.router_perspective_query_api import router as perspective_query_router
from app.router_perspective_admin_api import router as perspective_admin_router
from app.router_ask_answer_api import router as ask_answer_router
from app.router_server_cve_search_api import router as server_cve_search_router
from app.router_cve_severity_rollup_api import router as cve_severity_rollup_router
from app.router_freshness_metadata_api import router as freshness_metadata_router
from app.router_axis_evidence_api import router as axis_evidence_router
from app.router_scorecard_badge_api import router as scorecard_badge_router
from app.router_cadence_job_health_api import router as cadence_job_health_router
from app.router_perspective_event_rollup_api import router as perspective_event_rollup_router
from app.router_registry_source_freshness_report import router as registry_source_freshness_report_router
from app.router_dispute_backlog_summary_api import router as dispute_backlog_summary_router
from app.router_scoring_wave_cost_ledger_api import router as scoring_wave_cost_ledger_router
from app.router_harvest_lane_throughput_report import router as harvest_lane_throughput_report_router
from app.router_sprint_progress_dashboard_api import router as sprint_progress_dashboard_router
from app.router_cadence_job_sla_report import router as cadence_job_sla_report_router
from app.router_family_coverage_progress_api import router as family_coverage_progress_router
from app.router_family_first_wave_planner import router as family_first_wave_planner_router
from app.router_never_scored_burndown_api import router as never_scored_burndown_router
from app.router_wave_import_axis_drift_report import router as wave_import_axis_drift_report_router
from app.router_wedge_spend_ledger_report import router as wedge_spend_ledger_report_router
from app.router_directive_queue_health_api import router as directive_queue_health_router
from app.router_family_rollup_api import router as family_rollup_router
from app.router_score_change_timeline_api import router as score_change_timeline_router
from app.router_wave_refresh_verification_report import router as wave_refresh_verification_report_router
from app.router_ladder_rung_convergence_report import router as ladder_rung_convergence_report_router
from app.router_canonical_family_drift_probe import router as canonical_family_drift_probe_router
from app.router_fire_score import router as fire_score_router
from app.router_finalize_score import router as finalize_score_router
from app.router_score_run_ledger_writer import router as score_run_ledger_writer_router
from app.router_run_reconciliation_report import router as run_reconciliation_report_router
from app.router_import_row_delta_audit import router as import_row_delta_audit_router
from app.router_risk_tier_threshold_calibration_probe import router as risk_tier_threshold_calibration_probe_router
from app.router_axis_change_attribution_probe import router as axis_change_attribution_probe_router
from app.router_deferred_router_triage_report import router as deferred_router_triage_report_router
from app.router_registry_ingest_anomaly_report import router as registry_ingest_anomaly_report_router

ALL_ROUTERS = [
    ("/verdict", verdict_router),
    ("/registry", registry_router),
    ("/verdict/breakdown", verdict_breakdown_router),
    ("/verdict/axis-detail", verdict_axis_detail_router),
    ("/server/composite-risk-ranking", server_composite_risk_ranking_router),
    ("/dispute/detail", dispute_detail_router),
    ("/risk-tier/threshold", risk_tier_threshold_router),
    ("/audit/log-query", audit_log_query_router),
    ("/cve/facet-compile", cve_facet_compile_router),
    ("/verdict/export", verdict_export_router),
    ("/server/risk-tier-export", server_risk_tier_export_router),
    ("/server/scorecard", server_scorecard_router),
    ("/overview/dashboard", overview_dashboard_router),
    ("/dashboard/summary", dashboard_summary_router),
    ("/verdict/view", verdict_view_router),
    ("/org/entity-search", org_entity_search_router),
    ("/perspective/query", perspective_query_router),
    ("/perspective/admin", perspective_admin_router),
    ("/ask/answer", ask_answer_router),
    ("/server/cve-search", server_cve_search_router),
    ("/cve/severity-rollup", cve_severity_rollup_router),
    ("/freshness/metadata", freshness_metadata_router),
    ("/axis/evidence", axis_evidence_router),
    ("/scorecard/badge", scorecard_badge_router),
    ("/cadence/job-health", cadence_job_health_router),
    ("/perspective/event-rollup", perspective_event_rollup_router),
    ("/registry/source-freshness-report", registry_source_freshness_report_router),
    ("/dispute/backlog-summary", dispute_backlog_summary_router),
    ("/scoring/wave-cost-ledger", scoring_wave_cost_ledger_router),
    ("/harvest/lane-throughput-report", harvest_lane_throughput_report_router),
    ("/sprint/progress-dashboard", sprint_progress_dashboard_router),
    ("/cadence/job-sla-report", cadence_job_sla_report_router),
    ("/family/coverage-progress", family_coverage_progress_router),
    ("/family/first-wave-planner", family_first_wave_planner_router),
    ("/never-scored/burndown", never_scored_burndown_router),
    ("/wave/import-axis-drift-report", wave_import_axis_drift_report_router),
    ("/wedge/spend-ledger-report", wedge_spend_ledger_report_router),
    ("/directive/queue-health", directive_queue_health_router),
    ("/family/rollup", family_rollup_router),
    ("/score/change-timeline", score_change_timeline_router),
    ("/wave/refresh-verification-report", wave_refresh_verification_report_router),
    ("/ladder/rung-convergence-report", ladder_rung_convergence_report_router),
    ("/canonical/family-drift-probe", canonical_family_drift_probe_router),
    ("/fire/score", fire_score_router),
    ("/finalize/score", finalize_score_router),
    ("/score/run-ledger-writer", score_run_ledger_writer_router),
    ("/run/reconciliation-report", run_reconciliation_report_router),
    ("/import/row-delta-audit", import_row_delta_audit_router),
    ("/risk-tier/threshold-calibration-probe", risk_tier_threshold_calibration_probe_router),
    ("/axis/change-attribution-probe", axis_change_attribution_probe_router),
    ("/deferred/router-triage-report", deferred_router_triage_report_router),
    ("/registry/ingest-anomaly-report", registry_ingest_anomaly_report_router),
]

def get_router_by_prefix(prefix: str):
    for path, router in ALL_ROUTERS:
        if path == prefix:
            return router
    raise ValueError(f"No router registered at prefix {prefix}")

if __name__ == "__main__":
    from app.routers import ALL_ROUTERS, get_router_by_prefix
    assert len(ALL_ROUTERS) >= 30, f'Expected >=30 routers, got {len(ALL_ROUTERS)}'
    print(f'PASS: {len(ALL_ROUTERS)} routers registered')