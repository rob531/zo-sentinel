from fastapi import Depends
from sqlalchemy import text

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


ADVISORY_TABLE = VulnAdvisory.__tablename__
LINK_TABLE = VulnLink.__tablename__

_SEVERITY_BY_RANK = {
    4: "critical",
    3: "high",
    2: "medium",
    1: "low",
    0: "unknown",
}


def _hotspot_rows(session, limit: int):
    limit = max(1, min(int(limit), 500))
    sql = text(
        f"""
        SELECT
            a.package,
            a.ecosystem,
            COUNT(DISTINCT a.id) AS advisory_count,
            COALESCE(
                MAX(
                    CASE LOWER(COALESCE(a.severity, 'unknown'))
                        WHEN 'critical' THEN 4
                        WHEN 'high' THEN 3
                        WHEN 'medium' THEN 2
                        WHEN 'low' THEN 1
                        ELSE 0
                    END
                ),
                0
            ) AS severity_rank,
            COUNT(DISTINCT l.server_id) AS linked_server_count,
            COALESCE(AVG(l.match_confidence), 0.0) AS mean_match_confidence,
            MAX(a.published_at) AS latest_published_at
        FROM {ADVISORY_TABLE} AS a
        LEFT JOIN {LINK_TABLE} AS l
            ON l.advisory_id = a.id
        WHERE a.package IS NOT NULL
        GROUP BY a.package, a.ecosystem
        ORDER BY advisory_count DESC, a.package ASC, a.ecosystem ASC
        LIMIT :limit
        """
    )
    return session.execute(sql, {"limit": limit}).mappings().all()


def _total_hotspot_rows(session) -> int:
    sql = text(
        f"""
        SELECT COUNT(*) AS total_packages
        FROM (
            SELECT a.package, a.ecosystem
            FROM {ADVISORY_TABLE} AS a
            WHERE a.package IS NOT NULL
            GROUP BY a.package, a.ecosystem
        ) AS packages
        """
    )
    return int(session.execute(sql).scalar_one())


def _recent_advisories(session):
    sql = text(
        f"""
        WITH ranked AS (
            SELECT
                a.package,
                a.ecosystem,
                a.id,
                a.summary,
                a.severity,
                a.published_at,
                ROW_NUMBER() OVER (
                    PARTITION BY a.package, a.ecosystem
                    ORDER BY
                        CASE WHEN a.published_at IS NULL THEN 1 ELSE 0 END,
                        a.published_at DESC,
                        a.id DESC
                ) AS row_number
            FROM {ADVISORY_TABLE} AS a
            WHERE a.package IS NOT NULL
        )
        SELECT package, ecosystem, id, summary, severity, published_at
        FROM ranked
        WHERE row_number <= 5
        """
    )
    rows = session.execute(sql).mappings().all()
    grouped = {}
    for row in rows:
        key = (row["package"], row["ecosystem"])
        grouped.setdefault(key, []).append(
            {
                "id": row["id"],
                "summary": row["summary"],
                "severity": row["severity"],
                "published_at": row["published_at"],
            }
        )
    return grouped


def get_package_hotspots(session=Depends(get_session), limit: int = 50):
    hotspot_rows = _hotspot_rows(session, limit)
    recent = _recent_advisories(session)

    packages = []
    for row in hotspot_rows:
        package = row["package"]
        ecosystem = row["ecosystem"]
        packages.append(
            {
                "package": package,
                "ecosystem": ecosystem,
                "advisory_count": int(row["advisory_count"]),
                "max_severity": _SEVERITY_BY_RANK.get(
                    int(row["severity_rank"]), "unknown"
                ),
                "linked_server_count": int(row["linked_server_count"]),
                "mean_match_confidence": float(row["mean_match_confidence"]),
                "latest_published_at": row["latest_published_at"],
                "recent_advisories": recent.get((package, ecosystem), []),
            }
        )
    return packages


def get_package_hotspot_response(session=Depends(get_session), limit: int = 50):
    return {
        "packages": get_package_hotspots(session=session, limit=limit),
        "total_packages": _total_hotspot_rows(session),
    }


get_vuln_package_hotspots = get_package_hotspots
get_hotspots = get_package_hotspots
list_package_hotspots = get_package_hotspots


if __name__ == "__main__":
    print("PASS")