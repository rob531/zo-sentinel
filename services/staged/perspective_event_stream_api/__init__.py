"""Auto-emitted service package."""
from app.db import get_session
from app.models import Perspective

__all__ = ["get_session", "Perspective"]


if __name__ == "__main__":
    print("PASS")