import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.db import get_session
from app.models import VulnAdvisory


_DEFAULT_OUTPUT_PATH = "./outputs/reports/vuln_advisory_alias_overlap_report.json"


def _open_session():
    """Open the repository's session dependency without assuming its decorator style."""
    supplied = get_session()
    if hasattr(supplied, "query"):
        return supplied, None

    iterator = iter(supplied)
    try:
        session = next(iterator)
    except StopIteration as exc:
        raise RuntimeError("get_session did not yield a database session") from exc
    return session, iterator


def _close_session(session, iterator):
    try:
        if session is not None and hasattr(session, "close"):
            session.close()
    finally:
        if iterator is not None and hasattr(iterator, "close"):
            iterator.close()


def _row_value(row, index, name):
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return getattr(row, name, None)


def _decode_aliases(raw_aliases):
    if raw_aliases is None:
        return []

    if isinstance(raw_aliases, bytes):
        try:
            raw_aliases = raw_aliases.decode("utf-8")
        except UnicodeDecodeError:
            return []

    if isinstance(raw_aliases, str):
        value = raw_aliases.strip()
        if not value:
            return []

        if value[0] in "[{":
            try:
                decoded = json.loads(value)
            except (TypeError, ValueError):
                decoded = None
            if decoded is not None:
                return _decode_aliases(decoded)

        value = value.replace(";", ",")
        return [part.strip() for part in value.split(",") if part.strip()]

    if isinstance(raw_aliases, dict):
        return [str(alias).strip() for alias in raw_aliases.keys() if str(alias).strip()]

    if isinstance(raw_aliases, (list, tuple, set, frozenset)):
        aliases = []
        for item in raw_aliases:
            aliases.extend(_decode_aliases(item))
        return aliases

    try:
        return _decode_aliases(list(raw_aliases))
    except TypeError:
        value = str(raw_aliases).strip()
        return [value] if value else []


def _normalise_aliases(raw_aliases):
    aliases = set()
    for alias in _decode_aliases(raw_aliases):
        normalised = alias.strip().upper()
        if normalised:
            aliases.add(normalised)
    return aliases


def _string_value(value):
    if value is None:
        return ""
    return str(value).strip()


def build_vuln_advisory_alias_overlap_report(days: int = 90) -> dict:
    days = int(days)
    if days < 0:
        raise ValueError("days must be non-negative")

    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(days=days)

    session, iterator = _open_session()
    try:
        rows = (
            session.query(
                VulnAdvisory.id,
                VulnAdvisory.feed,
                VulnAdvisory.summary,
                VulnAdvisory.severity,
                VulnAdvisory.ecosystem,
                VulnAdvisory.package,
                VulnAdvisory.affected_ranges,
                VulnAdvisory.aliases,
                VulnAdvisory.source_url,
                VulnAdvisory.published_at,
                VulnAdvisory.fetched_at,
            )
            .filter(
                (VulnAdvisory.published_at >= cutoff)
                | (VulnAdvisory.fetched_at >= cutoff)
            )
            .all()
        )
    finally:
        _close_session(session, iterator)

    records = []
    alias_to_advisory_ids = defaultdict(set)
    advisory_aliases = {}

    for index, row in enumerate(rows):
        advisory_id = _string_value(_row_value(row, 0, "id"))
        if not advisory_id:
            continue

        aliases = _normalise_aliases(_row_value(row, 7, "aliases"))
        advisory_aliases[advisory_id] = aliases

        record = {
            "id": advisory_id,
            "feed": _string_value(_row_value(row, 1, "feed")),
            "severity": _string_value(_row_value(row, 3, "severity")),
            "package": _string_value(_row_value(row, 5, "package")),
        }
        records.append(record)

        for alias in aliases:
            alias_to_advisory_ids[alias].add(advisory_id)

    records_by_id = {record["id"]: record for record in records}
    collision_alias_names = sorted(
        alias
        for alias, advisory_ids in alias_to_advisory_ids.items()
        if len(advisory_ids) >= 2
    )
    collision_alias_set = set(collision_alias_names)

    alias_collisions = []
    collision_advisory_ids = set()

    for alias in collision_alias_names:
        advisory_ids = sorted(alias_to_advisory_ids[alias])
        collision_advisory_ids.update(advisory_ids)

        corresponding_records = [
            records_by_id[advisory_id]
            for advisory_id in advisory_ids
            if advisory_id in records_by_id
        ]
        feeds = sorted(
            {
                record["feed"]
                for record in corresponding_records
                if record["feed"]
            }
        )
        packages = sorted(
            {
                record["package"]
                for record in corresponding_records
                if record["package"]
            }
        )
        severities = sorted(
            {
                record["severity"]
                for record in corresponding_records
                if record["severity"]
            }
        )

        alias_collisions.append(
            {
                "alias": alias,
                "advisory_ids": advisory_ids,
                "feeds": feeds,
                "packages": packages,
                "severities": severities,
            }
        )

    orphan_alias_advisories = sorted(
        advisory_id
        for advisory_id, aliases in advisory_aliases.items()
        if aliases and any(alias not in collision_alias_set for alias in aliases)
    )

    return {
        "days": days,
        "generated_at": generated_at.isoformat(),
        "alias_collisions": alias_collisions,
        "totals": {
            "advisories": len(records),
            "aliases": len(alias_to_advisory_ids),
            "collision_aliases": len(alias_collisions),
            "collision_advisories": len(collision_advisory_ids),
        },
        "orphan_alias_advisories": orphan_alias_advisories,
    }


def _write_report(report: dict, out_path: str | None = None) -> str:
    path = _DEFAULT_OUTPUT_PATH if out_path is None else out_path
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    return path


def write_vuln_advisory_alias_overlap_report(
    out_path: str | None = None,
    days: int = 90,
) -> str:
    report = build_vuln_advisory_alias_overlap_report(days=days)
    return _write_report(report, out_path)


def main():
    from fastapi import Depends, FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id TEXT PRIMARY KEY,
                    feed TEXT NOT NULL,
                    summary TEXT,
                    severity TEXT,
                    ecosystem TEXT,
                    package TEXT,
                    affected_ranges TEXT,
                    aliases TEXT,
                    source_url TEXT,
                    published_at TIMESTAMP,
                    fetched_at TIMESTAMP
                )
                """
            )
        )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        seeded_rows = [
            {
                "id": "advisory-nvd-1",
                "feed": "nvd",
                "summary": "First seeded advisory",
                "severity": "HIGH",
                "ecosystem": "npm",
                "package": "example-package",
                "affected_ranges": json.dumps([{"introduced": "0"}]),
                "aliases": json.dumps(["CVE-2026-0001"]),
                "source_url": "https://example.test/nvd/1",
                "published_at": now,
                "fetched_at": now,
            },
            {
                "id": "advisory-ghsa-1",
                "feed": "ghsa",
                "summary": "Second seeded advisory",
                "severity": "CRITICAL",
                "ecosystem": "npm",
                "package": "example-package",
                "affected_ranges": json.dumps([{"introduced": "0"}]),
                "aliases": json.dumps(["CVE-2026-0001"]),
                "source_url": "https://example.test/ghsa/1",
                "published_at": now,
                "fetched_at": now,
            },
            {
                "id": "advisory-unique-1",
                "feed": "osv",
                "summary": "Unique seeded advisory",
                "severity": "MEDIUM",
                "ecosystem": "pypi",
                "package": "unique-package",
                "affected_ranges": json.dumps([{"introduced": "1.0.0"}]),
                "aliases": json.dumps(["GHSA-unique-0001"]),
                "source_url": "https://example.test/osv/1",
                "published_at": now,
                "fetched_at": now,
            },
        ]

        for row in seeded_rows:
            connection.execute(
                text(
                    """
                    INSERT INTO vuln_advisories (
                        id,
                        feed,
                        summary,
                        severity,
                        ecosystem,
                        package,
                        affected_ranges,
                        aliases,
                        source_url,
                        published_at,
                        fetched_at
                    ) VALUES (
                        :id,
                        :feed,
                        :summary,
                        :severity,
                        :ecosystem,
                        :package,
                        :affected_ranges,
                        :aliases,
                        :source_url,
                        :published_at,
                        :fetched_at
                    )
                    """
                ),
                row,
            )

    self_test_app = FastAPI()
    original_get_session = get_session

    @self_test_app.get("/self-test")
    def self_test_route(session=Depends(original_get_session)):
        return {"ok": True}

    def override_get_session():
        with SessionLocal() as session:
            yield session

    self_test_app.dependency_overrides[original_get_session] = override_get_session
    globals()["get_session"] = override_get_session

    try:
        report = build_vuln_advisory_alias_overlap_report(days=90)

        assert report["totals"]["advisories"] == 3
        assert report["totals"]["collision_aliases"] == 1
        assert len(report["alias_collisions"]) == 1
        assert len(report["alias_collisions"][0]["advisory_ids"]) == 2
        json.dumps(report, ensure_ascii=False)

        write_vuln_advisory_alias_overlap_report(days=90)
    finally:
        globals()["get_session"] = original_get_session
        self_test_app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    main()