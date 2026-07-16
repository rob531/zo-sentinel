import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import duckdb
from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusIndex

logger = logging.getLogger(__name__)

class AskCorpusHealthMonitor:
    def __init__(self, max_size_mb: int = 100, max_age_hours: int = 24):
        self.max_size_mb = max_size_mb
        self.max_age_hours = max_age_hours

    def check_table_health(self, db: Session = Depends(get_session)) -> bool:
        try:
            # Get table size and last indexed time
            con = duckdb.connect(database=':memory:')
            con.execute(f"ATTACH DATABASE '{db.engine.url.database}' AS app_db")

            # Get table size in MB
            size_query = """
            SELECT
                SUM(blocks) * 8 AS size_kb
            FROM
                pg_class c
            JOIN
                pg_namespace n ON n.oid = c.relnamespace
            WHERE
                n.nspname = 'public'
                AND c.relname = 'ask_corpus_index'
            """
            size_result = con.execute(size_query).fetchone()
            size_mb = size_result[0] / 1024 if size_result else 0

            # Get last indexed time
            last_indexed_query = """
            SELECT MAX(indexed_at)
            FROM ask_corpus_index
            """
            last_indexed_result = con.execute(last_indexed_query).fetchone()
            last_indexed = last_indexed_result[0] if last_indexed_result else None

            con.close()

            # Check health conditions
            size_healthy = size_mb <= self.max_size_mb
            age_healthy = (
                last_indexed and
                (datetime.now() - last_indexed) <= timedelta(hours=self.max_age_hours)
            )

            if not size_healthy:
                logger.warning(f"AskCorpusIndex table size exceeds limit: {size_mb:.2f} MB > {self.max_size_mb} MB")
            if not age_healthy:
                logger.warning(f"AskCorpusIndex table not updated in {self.max_age_hours} hours")

            return size_healthy and age_healthy

        except Exception as e:
            logger.error(f"Error checking AskCorpusIndex health: {str(e)}")
            return False

def get_health_monitor() -> AskCorpusHealthMonitor:
    return AskCorpusHealthMonitor()

if __name__ == "__main__":
    # Simulate a table health check
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    test_engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test table
    SessionLocal().execute("""
    CREATE TABLE ask_corpus_index (
        id INTEGER PRIMARY KEY,
        content TEXT,
        indexed_at TIMESTAMP
    )
    """)

    # Insert test data
    test_session = SessionLocal()
    test_session.execute("""
    INSERT INTO ask_corpus_index (content, indexed_at)
    VALUES ('test content', datetime('now', '-1 hour'))
    """)
    test_session.commit()

    # Run health check
    monitor = AskCorpusHealthMonitor(max_size_mb=1, max_age_hours=2)
    healthy = monitor.check_table_health()

    print("PASS" if healthy else "FAIL")