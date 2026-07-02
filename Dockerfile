# Zo-Sentinel app -- single container for Heroku (container stack) or Cloud Run.
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY app/requirements.txt /srv/app/requirements.txt
RUN pip install --no-cache-dir -r /srv/app/requirements.txt
COPY app /srv/app
COPY verdict_breakdown_api.py server_compare_api.py trust_gating_override.py score_dispute_api.py /srv/
# v1.1 Perspectives + v2 Ask slice (feature routers + root-served views)
COPY facet_enum_service.py perspective_model.py perspective_query_api.py perspective_admin_api.py perspective_diff_service.py ask_corpus_indexer.py ask_retrieval_service.py ask_answer_api.py /srv/
COPY perspective_tree_view.html ask_search_view.html roadmap_announcement.html /srv/
COPY migrations /srv/migrations
COPY alembic.ini /srv/alembic.ini
EXPOSE 8000
# PORT is injected by the platform (Heroku/Cloud Run); default 8000 locally.
CMD ["sh", "-c", "gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:${PORT:-8000} -w ${WEB_CONCURRENCY:-2} --timeout 120"]
