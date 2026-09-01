# Zo-Sentinel app -- single container for Heroku (container stack) or Cloud Run.
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY app/requirements.txt /srv/app/requirements.txt
RUN pip install --no-cache-dir -r /srv/app/requirements.txt
COPY app /srv/app
COPY verdict_breakdown_api.py server_compare_api.py trust_gating_override.py score_dispute_api.py /srv/
# v1.1 Perspectives + v2 Ask slice (feature routers + root-served views)
COPY facet_enum_service.py perspective_model.py perspective_query_api.py perspective_admin_api.py perspective_diff_service.py ask_corpus_indexer.py ask_retrieval_service.py ask_answer_api.py dashboard_summary_api.py vuln_identity.py vuln_osv_ingestor.py vuln_registry_linker.py vuln_exposure_api.py config_scan_api.py otx_threat_refs.py vuln_pkg_enricher.py \
     freshness_metadata_api.py vuln_facet_extension.py vuln_coverage_sla_api.py \
     cadence_admin_api.py scoring_freshness_surface.py runtime_deploy_info_endpoint.py \
     freshness_gate.py freshness_policy_api.py /srv/
# policy module (kill-switch chain) -- without this the vuln surfaces are
# permanently fail-closed on Fly (found 2026-07-04: ZO_VULN_ENABLED had no lever)
COPY zo_sentinel/__init__.py zo_sentinel/policy.py zo_sentinel/policy_defaults.toml /srv/zo_sentinel/

# FU-102: root modules registered in services/active/ but absent from the image.
# Without these the app mounts them at build time and prod raises ModuleNotFoundError
# (7 failures live on /spine/health since v64, 2026-07-25). Keep this list in sync
# with services/active/*/ -- a service dir with a root <name>.py MUST be COPY'd.
COPY entity_report_exporter.py org_api_key_manager.py org_entity_search_api.py overview_dashboard_api.py server_axis_scores_summary_router.py threat_intel_summary_api.py verdict_watchlist_service.py /srv/
COPY perspective_tree_view.html ask_search_view.html roadmap_announcement.html scan_view.html server_threat_intel_view.html /srv/
COPY migrations /srv/migrations
COPY alembic.ini /srv/alembic.ini
# FU-239: operational tools must be IN the image, not only in the repo.
# tools/clerk_reconcile.py is the negative control over the live Clerk
# webhook -- it landed in #2700 and was maintained across #2705 and #2707
# without ever executing once, because `ls /srv/tools` returned No such
# file. A merged correctness fix is not a running one. The away window
# (2026-08-07..08-30) is the whole reason that job exists; without this
# line it would emit 23 consecutive silent nights, and its own contract
# reads silence as health.
COPY tools /srv/tools

# FU-217: the SOA half of the FU-102 class. Every service under services/staged/
# declares `import_path = services.active.<name>.router`, and until this line
# there was no COPY token naming `services` at any depth -- so the promoter
# (tools/promote_staged_to_active.py -> tools/image_ship_check.would_be_shipped)
# correctly HELD every one of them: promoting into an image with no services/
# tree is a guaranteed ModuleNotFoundError at mount, i.e. a repeat of v64.
# Measured 2026-08-01T14:32Z on runtime b1c0d758: 300 candidates, 0 PROMOTE,
# 300 HOLD, of which 267 were this bucket. There was no COPY-list ENTRY to add a
# service to, because there was no COPY-list for services at all.
#
# ACTIVE ONLY, DELIBERATELY. services/staged/ is 262 dirs of code that has not
# passed a liveness contract; shipping it into the prod image would put
# un-gated modules one import away from a mounted app for no benefit. Promotion
# MOVES a directory staged -> active, so the build that follows a promotion
# carries the promoted service and nothing else. `services/__init__.py` is
# copied on its own so that `services` is an importable package without
# `services.staged` existing in the image.
COPY services/__init__.py /srv/services/
COPY services/active /srv/services/active
# Build identity for /version (runtime_deploy_info_endpoint): pass
#   --build-arg GIT_SHA=$(git rev-parse HEAD) --build-arg BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
# at deploy (flyctl deploy does this from the runbook). Defaults stay 'unknown'.
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ENV GIT_SHA=$GIT_SHA BUILD_TIME=$BUILD_TIME

EXPOSE 8000
# PORT is injected by the platform (Heroku/Cloud Run); default 8000 locally.
CMD ["sh", "-c", "gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:${PORT:-8000} -w ${WEB_CONCURRENCY:-2} --timeout 120"]
