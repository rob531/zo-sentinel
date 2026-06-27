breaker_action = {
    "asset_name": "overview_dashboard_filter.js",
    "action_type": "investigate",
    "rationale": "The frontend asset `overview_dashboard_filter.js` is currently failing Gate 8 (attempts=1/3). With the circuit breaker tripped, a rebuild is blocked. An investigation is needed to diagnose the root cause of the failure without attempting a rebuild. This directive does NOT rebuild overview_dashboard_filter.js; it triggers a breaker workflow.",
    "proposed_by": "directive_architect",
    "proposed_at": "2026-06-26T10:26:21.904140+00:00",
}