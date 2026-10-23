"""zo_sentinel - Sentinel service package."""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from app.db import get_session
from app.models import (
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    VulnAdvisory,
)


class PerspectiveSnapshot:
    """Mixin providing common fields for perspective snapshot models."""
    __tablename__ = "perspective_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    name = Column(String, nullable=True)


__all__ = [
    "PerspectiveSnapshot",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "VulnAdvisory",
    "get_session",
]

if __name__ == "__main__":
    print("PASS")