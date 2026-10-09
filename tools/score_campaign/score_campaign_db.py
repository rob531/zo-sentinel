#!/usr/bin/env python3
"""score_campaign_db.py -- the one DB seam the Fly-PG scoring campaign tools share.

WHY A SEAM: the exporter and ingestor must run against two backends that differ
only in dialect, never in logic:

  * production  -- Fly Postgres (`mcplookup-db`), reached THROUGH `fly proxy`
                   (default 127.0.0.1:15432). The DSN carries user/pass/db; the
                   actual socket is the local proxy (mirrors
                   tools/rescore/apply_risk_tier_backfill.py).
  * hermetic    -- a throwaway sqlite file the two-pole test seeds and asserts
                   on, with NO proxy, NO credentials, NO network.

Keeping the dialect difference HERE (placeholder style, fast bulk-insert path)
lets the campaign logic be exercised end-to-end by the sqlite gate and shipped
unchanged against Fly PG. psycopg2 is imported LAZILY so py_compile and the
sqlite test never require it.

DSN forms accepted:
  sqlite:///abs/or/rel/path.db   |  sqlite:relative.db  |  *.db / *.sqlite path
  postgresql://user:pass@host/db |  postgres://user:pass@host/db
"""
from __future__ import annotations

import re
import sqlite3


def is_sqlite_dsn(dsn: str) -> bool:
    d = (dsn or "").strip()
    return d.startswith("sqlite:") or d.endswith((".db", ".sqlite", ".sqlite3"))


def _sqlite_path(dsn: str) -> str:
    d = dsn.strip()
    if d.startswith("sqlite:///"):
        return d[len("sqlite:///"):]
    if d.startswith("sqlite://"):
        return d[len("sqlite://"):]
    if d.startswith("sqlite:"):
        return d[len("sqlite:"):]
    return d


class DB:
    """Thin dialect-neutral wrapper. SQL is written with %s placeholders
    (postgres style); sqlite gets them rewritten to ? at execute time."""

    def __init__(self, conn, backend: str):
        self.conn = conn
        self.backend = backend
        self.cur = conn.cursor()

    @classmethod
    def connect(cls, dsn: str, host: str = "127.0.0.1", port: int = 15432) -> "DB":
        if is_sqlite_dsn(dsn):
            conn = sqlite3.connect(_sqlite_path(dsn))
            return cls(conn, "sqlite")
        m = re.match(r"postgres(?:ql)?://([^:]+):([^@]+)@[^/]+/(\w+)", dsn.strip())
        if not m:
            raise SystemExit("FATAL: unparseable postgres DSN (want postgresql://user:pass@host/db)")
        import psycopg2  # lazy: only the prod path needs the driver
        conn = psycopg2.connect(host=host, port=port, dbname=m.group(3),
                                user=m.group(1), password=m.group(2),
                                connect_timeout=15)
        return cls(conn, "postgres")

    def q(self, sql: str) -> str:
        return sql if self.backend == "postgres" else sql.replace("%s", "?")

    def execute(self, sql: str, params=()):
        self.cur.execute(self.q(sql), params)
        return self.cur

    def fetchall(self):
        return self.cur.fetchall()

    def insert_rows(self, table: str, columns, rows) -> int:
        """Bulk insert. execute_values on postgres (the proven fast path from
        weekly_rescore), executemany on sqlite. Returns rows inserted."""
        rows = list(rows)
        if not rows:
            return 0
        cols = ", ".join(columns)
        if self.backend == "postgres":
            from psycopg2.extras import execute_values
            execute_values(self.cur, f"insert into {table} ({cols}) values %s", rows)
        else:
            ph = ", ".join("?" * len(columns))
            self.cur.executemany(f"insert into {table} ({cols}) values ({ph})", rows)
        return len(rows)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
