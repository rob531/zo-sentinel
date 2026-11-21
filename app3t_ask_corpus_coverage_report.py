from datetime import date, datetime


def _server_id(value: object) -> str:
    return str(value) if value is not None else ""


def _is_valid_iso_timestamp(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, (datetime, date)):
        return True
    if not isinstance(value, str):
        return False

    candidate = value.strip()
    if not candidate:
        return False
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"

    try:
        datetime.fromisoformat(candidate)
    except (TypeError, ValueError):
        return False
    return True


def build_report(registry_rows: list[dict], corpus_rows: list[dict]) -> dict:
    registry_by_id: dict[str, str | None] = {}
    for row in registry_rows:
        server_id = _server_id(row.get("server_id"))
        if server_id not in registry_by_id:
            name = row.get("name")
            registry_by_id[server_id] = name if name is None else str(name)

    corpus_server_ids: set[str] = set()
    orphan_server_ids: set[str] = set()
    stale_docs = 0
    missing_snippet_docs = 0

    for row in corpus_rows:
        server_id = _server_id(row.get("server_id"))
        corpus_server_ids.add(server_id)
        if server_id not in registry_by_id:
            orphan_server_ids.add(server_id)

        if not _is_valid_iso_timestamp(row.get("indexed_at")):
            stale_docs += 1

        snippet = row.get("snippet")
        if snippet is None or not str(snippet).strip():
            missing_snippet_docs += 1

    total_servers = len(registry_by_id)
    indexed_server_ids = corpus_server_ids.intersection(registry_by_id)
    indexed_servers = len(indexed_server_ids)
    unindexed_servers = total_servers - indexed_servers
    coverage_pct = (
        round((indexed_servers / total_servers) * 100.0, 2)
        if total_servers
        else 0.0
    )

    top_unindexed = [
        {
            "server_id": server_id,
            "name": registry_by_id[server_id],
        }
        for server_id in sorted(registry_by_id.keys() - indexed_server_ids)[:10]
    ]

    return {
        "total_servers": total_servers,
        "indexed_servers": indexed_servers,
        "unindexed_servers": unindexed_servers,
        "coverage_pct": coverage_pct,
        "orphan_corpus_servers": len(orphan_server_ids),
        "stale_docs": stale_docs,
        "missing_snippet_docs": missing_snippet_docs,
        "top_unindexed": top_unindexed,
    }


def render_text(report: dict) -> str:
    lines = [
        "Ask Corpus Coverage Report",
        f"total_servers: {report['total_servers']}",
        f"indexed_servers: {report['indexed_servers']}",
        f"unindexed_servers: {report['unindexed_servers']}",
        f"coverage_pct: {report['coverage_pct']:.2f}",
        f"orphan_corpus_servers: {report['orphan_corpus_servers']}",
        f"stale_docs: {report['stale_docs']}",
        f"missing_snippet_docs: {report['missing_snippet_docs']}",
        "top_unindexed:",
    ]

    if not report["top_unindexed"]:
        lines.append("  (none)")
    else:
        for item in report["top_unindexed"]:
            name = item["name"]
            rendered_name = "null" if name is None else name
            lines.append(f"  - server_id: {item['server_id']}")
            lines.append(f"    name: {rendered_name}")

    return "\n".join(lines)


if __name__ == "__main__":
    registry_rows = [
        {"server_id": "srv-a", "name": "Server A"},
        {"server_id": "srv-b", "name": "Server B"},
        {"server_id": "srv-c", "name": "Server C"},
        {"server_id": "srv-d", "name": "Server D"},
    ]
    corpus_rows = [
        {
            "server_id": "srv-a",
            "snippet": "Indexed server A",
            "terms": "a",
            "content_hash": "hash-a",
            "indexed_at": "2025-01-01T12:00:00Z",
        },
        {
            "server_id": "srv-b",
            "snippet": "   ",
            "terms": "b",
            "content_hash": "hash-b",
            "indexed_at": None,
        },
        {
            "server_id": "srv-orphan",
            "snippet": "Orphan document",
            "terms": "orphan",
            "content_hash": "hash-orphan",
            "indexed_at": "2025-01-02T12:00:00Z",
        },
    ]

    report = build_report(registry_rows, corpus_rows)
    assert report["total_servers"] == 4
    assert report["indexed_servers"] == 2
    assert report["unindexed_servers"] == 2
    assert report["coverage_pct"] == 50.0
    assert report["orphan_corpus_servers"] == 1
    assert report["stale_docs"] == 1
    assert report["missing_snippet_docs"] == 1
    assert "coverage_pct" in render_text(report)
    assert "50.00" in render_text(report)
    assert build_report([], [])["coverage_pct"] == 0.0
    print("PASS")