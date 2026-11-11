from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry as McpServerRegistry

try:
    from .logic import get_risk_register_growth
except ImportError:
    try:
        from .logic import get_growth as get_risk_register_growth
    except ImportError:
        from .logic import get_growth_data as get_risk_register_growth


router = APIRouter(prefix="/api", tags=["risk-register"])


@router.get("/risk-register/growth")
def get_risk_register_growth_api(
    days: int = Query(default=30),
    session: Session = Depends(get_session),
):
    effective_days = max(1, min(int(days), 365))
    return get_risk_register_growth(session, effective_days)


if __name__ == "__main__":
    print("PASS")