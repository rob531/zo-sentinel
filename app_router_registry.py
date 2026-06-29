from fastapi import FastAPI
from app.routers import (
    verdict_view_api,
    dashboard_summary_api,
    org_entity_search_api,
    entity_report_exporter,
    verdict_watchlist_service,
    oauth_login_service,
    rbac_enforcer,
    product_audit_log,
    org_api_key_manager
)

def include_app_routers(app: FastAPI):
    app.include_router(verdict_view_api.router, prefix="/verdict-view", tags=["Verdict View"])
    app.include_router(dashboard_summary_api.router, prefix="/dashboard-summary", tags=["Dashboard Summary"])
    app.include_router(org_entity_search_api.router, prefix="/org-entity-search", tags=["Organization Entity Search"])
    app.include_router(entity_report_exporter.router, prefix="/entity-report-exporter", tags=["Entity Report Exporter"])
    app.include_router(verdict_watchlist_service.router, prefix="/verdict-watchlist", tags=["Verdict Watchlist Service"])
    app.include_router(oauth_login_service.router, prefix="/oauth-login", tags=["OAuth Login Service"])
    app.include_router(rbac_enforcer.router, prefix="/rbac-enforcer", tags=["RBAC Enforcer"])
    app.include_router(product_audit_log.router, prefix="/product-audit-log", tags=["Product Audit Log"])
    app.include_router(org_api_key_manager.router, prefix="/org-api-key-manager", tags=["Organization API Key Manager"])