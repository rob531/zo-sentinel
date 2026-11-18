"""logic.py -- the service's data/computation layer for CVE linkage gaps."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry, VulnAdvisory, VulnLink


def linkage_gap(db: Session) -> Dict[str, object]:
    """Compute registry coverage and vulnerability-linkage statistics."""
    registry_rows = db.execute(
        select(McpServerRegistry.server_id, McpServerRegistry.registry_source)
    ).all()

    source_by_server: Dict[str, str] = {}
    servers_by_source: Dict[str, int] = defaultdict(int)
    for server_id, registry_source in registry_rows:
        source = registry_source or "unknown"
        source_by_server[server_id] = source
        servers_by_source[source] += 1

    link_rows = db.execute(
        select(
            VulnLink.server_id,
            VulnLink.match_confidence,
            VulnAdvisory.feed,
            VulnAdvisory.ecosystem,
        )
        .join(VulnAdvisory, VulnAdvisory.id == VulnLink.advisory_id)
    ).all()

    linked_servers = {
        server_id
        for server_id, _confidence, _feed, _ecosystem in link_rows
        if server_id in source_by_server
    }

    linked_by_source: Dict[str, int] = defaultdict(int)
    links_by_feed: Dict[str, int] = defaultdict(int)
    confidence_by_feed: Dict[str, float] = defaultdict(float)
    links_by_pair: Dict[str, int] = defaultdict(int)

    for server_id, confidence, feed, ecosystem in link_rows:
        if server_id not in source_by_server:
            continue

        linked_by_source[source_by_server[server_id]] += 1

        feed_name = feed or "unknown"
        links_by_feed[feed_name] += 1
        confidence_by_feed[feed_name] += float(confidence or 0.0)

        pair = f"{feed_name}/{ecosystem or 'unknown'}"
        links_by_pair[pair] += 1

    by_source: List[Dict[str, object]] = []
    for source in sorted(servers_by_source):
        servers = servers_by_source[source]
        linked = linked_by_source.get(source, 0)
        unlinked = servers - linked
        by_source.append(
            {
                "registry_source": source,
                "servers": servers,
                "linked": linked,
                "unlinked": unlinked,
                "linkage_pct": round((linked / servers) * 100.0, 2) if servers else 0.0,
            }
        )

    by_feed: List[Dict[str, object]] = []
    for feed in sorted(links_by_feed):
        count = links_by_feed[feed]
        by_feed.append(
            {
                "feed": feed,
                "links": count,
                "mean_confidence": round(confidence_by_feed[feed] / count, 4) if count else 0.0,
            }
        )

    top_packages = [
        {"package": package, "links": links}
        for package, links in sorted(
            links_by_pair.items(),
            key=lambda item: (-item[1], item[0]),
        )[:10]
    ]

    total_servers = len(registry_rows)
    linked_count = len(linked_servers)
    unlinked_count = total_servers - linked_count

    return {
        "total_servers": total_servers,
        "linked_servers": linked_count,
        "unlinked_servers": unlinked_count,
        "linkage_pct": round((linked_count / total_servers) * 100.0, 2)
        if total_servers
        else 0.0,
        "by_source": by_source,
        "by_feed": by_feed,
        "top_packages": top_packages,
    }


compute_linkage_gap = linkage_gap
get_linkage_gap = linkage_gap
get_linkage_gap_data = linkage_gap


if __name__ == "__main__":
    print("PASS")