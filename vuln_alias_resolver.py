from typing import Dict, List, Set, Tuple
from dataclasses import dataclass
from collections import defaultdict
import requests
from app.db import get_session
from app.models import Advisory
from fastapi import Depends
from sqlalchemy.orm import Session

@dataclass
class AliasCluster:
    canonical_id: str
    member_ids: Set[str]
    sources: Set[str]

class UnionFind:
    def __init__(self):
        self.parent = {}
        self.rank = {}

    def find(self, x: str) -> str:
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x: str, y: str):
        root_x = self.find(x)
        root_y = self.find(y)

        if root_x != root_y:
            if self.rank[root_x] > self.rank[root_y]:
                self.parent[root_y] = root_x
            elif self.rank[root_x] < self.rank[root_y]:
                self.parent[root_x] = root_y
            else:
                self.parent[root_y] = root_x
                self.rank[root_x] += 1

def resolve_aliases(advisories: List[Advisory]) -> List[AliasCluster]:
    uf = UnionFind()
    id_to_advisory = {}
    id_to_source = {}

    for advisory in advisories:
        advisory_id = advisory.id
        aliases = advisory.aliases or []
        all_ids = [advisory_id] + aliases

        for id_ in all_ids:
            if id_ not in uf.parent:
                uf.parent[id_] = id_
                uf.rank[id_] = 0
                id_to_advisory[id_] = advisory
                id_to_source[id_] = advisory.source

        for id_ in all_ids:
            uf.union(id_, advisory_id)

    clusters = defaultdict(lambda: AliasCluster("", set(), set()))

    for id_ in uf.parent:
        root = uf.find(id_)
        clusters[root].member_ids.add(id_)
        clusters[root].sources.add(id_to_source[id_])

    for root in clusters:
        canonical_id = root
        if any(id_.startswith("CVE-") for id_ in clusters[root].member_ids):
            canonical_id = next(id_ for id_ in clusters[root].member_ids if id_.startswith("CVE-"))
        clusters[root].canonical_id = canonical_id

    return list(clusters.values())

def write_alias_clusters(clusters: List[AliasCluster]) -> None:
    for cluster in clusters:
        for member_id in cluster.member_ids:
            data = {
                "canonical_id": cluster.canonical_id,
                "member_id": member_id,
                "source": next(iter(cluster.sources))
            }
            requests.post("http://127.0.0.1:8772/query", json=data)

def get_advisories(db: Session = Depends(get_session)) -> List[Advisory]:
    return db.query(Advisory).all()

def main():
    from app.db import get_session
    from app.models import Advisory
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    db.execute("CREATE TABLE advisories (id TEXT PRIMARY KEY, aliases TEXT, source TEXT)")
    db.execute("INSERT INTO advisories VALUES ('GHSA-x', 'CVE-1', 'ghsa')")
    db.execute("INSERT INTO advisories VALUES ('OSV-y', 'CVE-1', 'osv')")
    db.execute("INSERT INTO advisories VALUES ('CVE-2', '', 'nvd')")
    db.execute("INSERT INTO advisories VALUES ('GHSA-z', '', 'ghsa')")

    def mock_get_session():
        return db

    app.dependency_overrides[get_session] = mock_get_session

    advisories = get_advisories(db)
    clusters = resolve_aliases(advisories)

    expected_clusters = [
        AliasCluster("CVE-1", {"GHSA-x", "OSV-y", "CVE-1"}, {"ghsa", "osv"}),
        AliasCluster("CVE-2", {"CVE-2"}, {"nvd"}),
        AliasCluster("GHSA-z", {"GHSA-z"}, {"ghsa"})
    ]

    assert len(clusters) == len(expected_clusters)
    for cluster, expected in zip(clusters, expected_clusters):
        assert cluster.canonical_id == expected.canonical_id
        assert cluster.member_ids == expected.member_ids
        assert cluster.sources == expected.sources

    print("PASS")

if __name__ == "__main__":
    main()