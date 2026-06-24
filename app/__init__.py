"""Zo-Sentinel 3-tier app package (application + presentation tiers).

Houses the assembled FastAPI app: settings, DB/session, ORM models, security
(JWT + hashing), RBAC, and the feature routers. The autonomous factory builds
single-file feature modules; this package is the assembly + infra layer that
turns them into a deployable service (see docs/zo_sentinel_deployment_readiness.md).
"""
__version__ = "0.1.0"

