"""#4089 verification probe (b): an active service that declares no router.

It is deliberately NOT listed in tools/spine_known_issues.json, so
check_routes() must record it as an UNDECLARED no-router skip and fail.
"""


def helper() -> str:
    return "no router here"
