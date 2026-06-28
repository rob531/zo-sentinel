from zo_sentinel.breaker_actions import BreakerAction

class InvestigateMCPAttestationReviewDashboardView(BreakerAction):
    """
    Quality-gate breaker action 'investigate' for mcp_attestation_review_dashboard_view.html.
    Rationale: Quarantined file with recent failures, needs investigation before any rebuild attempts.
    This directive does NOT rebuild mcp_attestation_review_dashboard_view.html; it triggers a breaker workflow.
    Proposed by directive_architect at 2026-06-27T17:38:17.103615+00:00.
    """

    def __init__(self):
        super().__init__(
            name="investigate_mcp_attestation_review_dashboard_view",
            description="Investigate mcp_attestation_review_dashboard_view.html due to recent failures.",
            target_file="mcp_attestation_review_dashboard_view.html",
            rationale="Quarantined file with recent failures, needs investigation before any rebuild attempts.",
            rebuild=False
        )

    def execute(self):
        """
        Execute the breaker action.
        """
        # Trigger the breaker workflow for investigation
        self.trigger_breaker_workflow()