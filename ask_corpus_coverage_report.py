from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional


STALE_AFTER_DAYS = 30
MAX_SAMPLE_SIZE = 10


def _parse_indexed_at(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_with_fetcher(
    db_fetcher: Callable[..., Any],
    registry_servers: list[str],
    corpus_docs: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    try:
        signature = inspect.signature(db_fetcher)
        signature.bind(registry_servers, corpus_docs)
    except (TypeError, ValueError):
        result = db_fetcher()
    else:
        result = db_fetcher(registry_servers, corpus_docs)

    if isinstance(result, dict):
        loaded_registry = result.get("registry_servers", result.get("registry", []))
        loaded_docs = result.get("corpus_docs", result.get("docs", []))
    elif isinstance(result, (tuple, list)) and len(result) == 2:
        loaded_registry, loaded_docs = result
    else:
        raise ValueError(
            "db_fetcher must return (registry_servers, corpus_docs) or a mapping"
        )

    return list(loaded_registry or []), list(loaded_docs or [])


def _has_terms(value: Any) -> bool:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return len(value) > 0
    return bool(value)


def report_ask_corpus_coverage(
    db_fetcher: Optional[Callable[..., Any]] = None,
    registry_servers: Optional[list[str]] = None,
    corpus_docs: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    registry = list(registry_servers or [])
    docs = list(corpus_docs or [])

    if db_fetcher is not None:
        registry, docs = _load_with_fetcher(db_fetcher, registry, docs)

    registry_ids = {
        server_id
        for server_id in registry
        if isinstance(server_id, str)
    }
    indexed_ids = {
        doc.get("server_id")
        for doc in docs
        if isinstance(doc, dict) and doc.get("server_id") in registry_ids
    }
    unindexed_ids = registry_ids - indexed_ids

    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=STALE_AFTER_DAYS)
    valid_dates: list[datetime] = []
    stale_docs = 0
    empty_term_docs = 0

    for doc in docs:
        if not isinstance(doc, dict):
            continue

        if not _has_terms(doc.get("terms")):
            empty_term_docs += 1

        indexed_at = _parse_indexed_at(doc.get("indexed_at"))
        if indexed_at is not None:
            valid_dates.append(indexed_at)
            if indexed_at < stale_cutoff:
                stale_docs += 1

    total_servers = len(registry_ids)
    indexed_servers = len(indexed_ids)
    unindexed_servers = total_servers - indexed_servers
    coverage_pct = (
        round((indexed_servers / total_servers) * 100, 1)
        if total_servers
        else 0.0
    )

    oldest = min(valid_dates).isoformat() if valid_dates else None
    newest = max(valid_dates).isoformat() if valid_dates else None

    return {
        "total_servers": total_servers,
        "indexed_servers": indexed_servers,
        "unindexed_servers": unindexed_servers,
        "coverage_pct": coverage_pct,
        "stale_docs": stale_docs,
        "empty_term_docs": empty_term_docs,
        "oldest_indexed_at": oldest,
        "newest_indexed_at": newest,
        "sample_unindexed": sorted(unindexed_ids)[:MAX_SAMPLE_SIZE],
    }


def _self_test() -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=365)).isoformat()
    recent = (now - timedelta(days=1)).isoformat()

    registry_servers = ["server-a", "server-b", "server-c"]
    corpus_docs = [
        {
            "server_id": "server-a",
            "terms": [],
            "indexed_at": recent,
            "content_hash": "hash-a",
        },
        {
            "server_id": "server-b",
            "terms": ["registry", "coverage"],
            "indexed_at": old,
            "content_hash": "hash-b",
        },
    ]

    report = report_ask_corpus_coverage(
        registry_servers=registry_servers,
        corpus_docs=corpus_docs,
    )

    assert report["total_servers"] == 3
    assert report["indexed_servers"] == 2
    assert report["unindexed_servers"] == 1
    assert abs(report["coverage_pct"] - 66.7) <= 0.1
    assert report["empty_term_docs"] == 1
    assert report["stale_docs"] == 1
    print("PASS")


if __name__ == "__main__":
    _self_test()