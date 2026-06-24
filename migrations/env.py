"""Alembic environment -- targets the app ORM metadata + the app DATABASE_URL.
`alembic upgrade head` runs as the Heroku release-phase / Cloud Run job on deploy.
"""
from logging.config import fileConfig

from alembic import context

from app.db import Base, DATABASE_URL, engine
from app import models  # noqa: F401  (register tables on Base.metadata)

config = context.config
if config.config_file_name:
    try:
        fileConfig(config.config_file_name)
    except Exception:
        pass

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=DATABASE_URL, target_metadata=target_metadata,
                      literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
