# breaker_actions/breaker_action_investigate_admin_submissions_listing_api.py

from zo_sentinel.breaker_actions import BreakerAction, BreakerActionType, BreakerActionTarget
from datetime import datetime

BREAKER_ACTION = BreakerAction(
    action_type=BreakerActionType.INVESTIGATE,
    target=BreakerActionTarget(file_path="admin_submissions_listing_api.py"),
    rationale=(
        "`admin_submissions_listing_api.py` is failing Gate 8 with `attempts=1/3`. "
        "An investigation is needed to diagnose the root cause of the failure and unblock its progress, "
        "as it is a critical component for admin functionality."
    ),
    proposed_by="directive_architect",
    proposed_timestamp=datetime.fromisoformat("2026-06-26T10:48:52.650314+00:00"),
)