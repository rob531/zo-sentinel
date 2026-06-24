# Zo-Sentinel app skeleton (deployable 3-tier service)

The `app/` package is the **assembly + infra layer** that turns the factory's
single-file feature modules into a runnable FastAPI service.

## Layout
- `app/settings.py` - env-driven config (JWT secret, DATABASE_URL, CORS).
- `app/db.py` - SQLAlchemy engine + `get_session` dependency (Postgres in deploy, sqlite in dev/CI).
- `app/models.py` - ORM: `orgs`, `users`, `api_keys` (multi-tenant core).
- `app/security.py` - password hashing (pbkdf2_sha256) + our-own JWT + `get_principal`.
- `app/rbac.py` - `require_role(min_role)` / `require_org` server-side dependencies.
- `app/auth.py` - `/auth/register|login|refresh|me|oauth/{provider}/callback`.
- `app/main.py` - the app: health + auth + RBAC demo routes; best-effort mounts of factory routers.
- `migrations/` - Alembic (baseline `0001_initial`); `alembic upgrade head` on deploy.

## Run locally
```
pip install -r app/requirements.txt
uvicorn app.main:app --reload      # sqlite dev DB auto-created
```

## Deploy
- **Heroku (container):** `heroku stack:set container` -> push. `Procfile` runs `alembic upgrade head`
  (release phase) then `gunicorn ... UvicornWorker`. Add `heroku-postgresql`; set `APP_JWT_SECRET`.
  `app.json` describes the one-click setup.
- **GCP Cloud Run:** build the `Dockerfile`, provision Cloud SQL (Postgres), set `DATABASE_URL` +
  `APP_JWT_SECRET` (Secret Manager), run `alembic upgrade head` as a migration job, deploy the image.

Same container for both; only the platform config differs. See `zo_sentinel_deployment_readiness.md`.
