"""
Admin Submissions Logic
"""
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import List, Optional

def get_submissions(session: Session, status_filter: Optional[str] = None) -> List[dict]:
    query = text("""
        SELECT 
            ms.submission_id,
            msr.server_id,
            msr.name,
            msr.registry_source,
            msr.first_seen,
            msr.risk_tier,
            ms.status
        FROM mcp_submissions ms
        JOIN mcp_server_registry msr ON ms.server_id = msr.server_id
    """)
    
    if status_filter:
        query = text("""
            SELECT 
                ms.submission_id,
                msr.server_id,
                msr.name,
                msr.registry_source,
                msr.first_seen,
                msr.risk_tier,
                ms.status
            FROM mcp_submissions ms
            JOIN mcp_server_registry msr ON ms.server_id = msr.server_id
            WHERE ms.status = :status_filter
        """)
        result = session.execute(query, {"status_filter": status_filter})
    else:
        result = session.execute(query)
    
    rows = result.fetchall()
    return [
        {
            "submission_id": row[0],
            "server_id": row[1],
            "name": row[2],
            "registry_source": row[3],
            "first_seen": str(row[4]) if row[4] else "",
            "risk_tier": row[5] or "unknown",
            "status": row[6]
        }
        for row in rows
    ]