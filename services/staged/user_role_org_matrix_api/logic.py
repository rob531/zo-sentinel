from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import User


def get_user_role_org_matrix(db: Session = Depends(get_session)) -> dict:
    grouped_rows = db.execute(
        text(
            """
            SELECT role, org_id, COUNT(*) AS n
            FROM users
            GROUP BY role, org_id
            ORDER BY n DESC
            """
        )
    ).mappings()

    totals = db.execute(
        text(
            """
            SELECT
                COUNT(*) AS total_users,
                COUNT(DISTINCT org_id) AS distinct_orgs
            FROM users
            """
        )
    ).mappings().one()

    return {
        "rows": [
            {
                "role": row["role"],
                "org_id": row["org_id"],
                "count": row["n"],
            }
            for row in grouped_rows
        ],
        "total_users": totals["total_users"],
        "distinct_orgs": totals["distinct_orgs"],
    }


if __name__ == "__main__":
    print("PASS")