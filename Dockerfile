# Zo-Sentinel app -- single container for Heroku (container stack) or Cloud Run.
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY app/requirements.txt /srv/app/requirements.txt
RUN pip install --no-cache-dir -r /srv/app/requirements.txt
COPY app /srv/app
COPY verdict_breakdown_api.py trust_gating_override.py /srv/
COPY migrations /srv/migrations
COPY alembic.ini /srv/alembic.ini
EXPOSE 8000
# PORT is injected by the platform (Heroku/Cloud Run); default 8000 locally.
CMD ["sh", "-c", "gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:${PORT:-8000} -w ${WEB_CONCURRENCY:-2} --timeout 120"]
