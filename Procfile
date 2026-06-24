release: alembic upgrade head
web: gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT -w ${WEB_CONCURRENCY:-2}
