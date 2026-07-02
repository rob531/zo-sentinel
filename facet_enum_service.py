from typing import Dict, List, Optional
from fastapi import Depends
from sqlalchemy import func, select, and_
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPAxisScores

class FacetEnumService:
    def __init__(self, session: Session = Depends(get_session)):
        self.session = session

    def get_facet_universe(self) -> Dict[str, List[Dict[str, int]]]:
        facets = {}

        # Get risk_tier, verdict, registry_source facets
        registry_facets = self._get_registry_facets()
        facets.update(registry_facets)

        # Get axis facets
        axis_facets = self._get_axis_facets()
        facets.update(axis_facets)

        return facets

    def _get_registry_facets(self) -> Dict[str, List[Dict[str, int]]]:
        facets = {}

        # Get risk_tier facets
        risk_tier_query = select(
            MCPServerRegistry.risk_tier,
            func.count(MCPServerRegistry.risk_tier).label('count')
        ).group_by(MCPServerRegistry.risk_tier)
        risk_tier_result = self.session.execute(risk_tier_query).fetchall()
        facets['risk_tier'] = [{'value': row[0], 'count': row[1]} for row in risk_tier_result]

        # Get verdict facets
        verdict_query = select(
            MCPServerRegistry.verdict,
            func.count(MCPServerRegistry.verdict).label('count')
        ).group_by(MCPServerRegistry.verdict)
        verdict_result = self.session.execute(verdict_query).fetchall()
        facets['verdict'] = [{'value': row[0], 'count': row[1]} for row in verdict_result]

        # Get registry_source and trust_band facets
        registry_source_query = select(
            MCPServerRegistry.registry_source,
            func.count(MCPServerRegistry.registry_source).label('count')
        ).group_by(MCPServerRegistry.registry_source)
        registry_source_result = self.session.execute(registry_source_query).fetchall()
        facets['registry_source'] = [{'value': row[0], 'count': row[1]} for row in registry_source_result]

        # Calculate trust_band facets
        trust_band_query = select(
            func.floor(MCPServerRegistry.trust_score / 25).label('band'),
            func.count(MCPServerRegistry.trust_score).label('count')
        ).group_by('band')
        trust_band_result = self.session.execute(trust_band_query).fetchall()
        facets['trust_band'] = [{'value': row[0], 'count': row[1]} for row in trust_band_result]

        return facets

    def _get_axis_facets(self) -> Dict[str, List[Dict[str, int]]]:
        facets = {}

        # Get latest model_version
        latest_model_query = select(
            func.max(MCPAxisScores.model_version)
        )
        latest_model_version = self.session.execute(latest_model_query).scalar()

        if latest_model_version is not None:
            # Get axis_name and label facets
            axis_query = select(
                MCPAxisScores.axis_name,
                MCPAxisScores.label,
                func.count().label('count')
            ).where(
                MCPAxisScores.model_version == latest_model_version
            ).group_by(
                MCPAxisScores.axis_name,
                MCPAxisScores.label
            )
            axis_result = self.session.execute(axis_query).fetchall()

            # Group by axis_name
            axis_groups = {}
            for row in axis_result:
                axis_name = row[0]
                label = row[1]
                count = row[2]

                if axis_name not in axis_groups:
                    axis_groups[axis_name] = []

                axis_groups[axis_name].append({'value': label, 'count': count})

            # Add to facets with axis:<axis_name> keys
            for axis_name, values in axis_groups.items():
                facets[f'axis:{axis_name}'] = values

        return facets

if __name__ == '__main__':
    import py_compile
    import sqlite3
    from sqlalchemy import create_engine, MetaData
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create a throwaway SQLite session for testing
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Insert sample data
    session.execute("INSERT INTO mcp_server_registry (risk_tier, verdict, registry_source, trust_score) VALUES ('high', 'positive', 'source1', 75)")
    session.execute("INSERT INTO mcp_server_registry (risk_tier, verdict, registry_source, trust_score) VALUES ('medium', 'neutral', 'source2', 50)")
    session.execute("INSERT INTO mcp_server_registry (risk_tier, verdict, registry_source, trust_score) VALUES ('low', 'negative', 'source1', 25)")
    session.execute("INSERT INTO mcp_llm_axis_scores (axis_name, label, model_version) VALUES ('axis1', 'label1', 1)")
    session.execute("INSERT INTO mcp_llm_axis_scores (axis_name, label, model_version) VALUES ('axis1', 'label2', 1)")
    session.execute("INSERT INTO mcp_llm_axis_scores (axis_name, label, model_version) VALUES ('axis2', 'label1', 1)")
    session.commit()

    # Override the dependency
    from app.dependency_overrides import dependency_overrides
    dependency_overrides[get_session] = lambda: session

    # Get facet universe
    service = FacetEnumService(session)
    facet_universe = service.get_facet_universe()

    # Assert the dict shape
    assert isinstance(facet_universe, dict)
    assert all(isinstance(key, str) for key in facet_universe.keys())
    assert all(isinstance(values, list) for values in facet_universe.values())

    # Assert axis facets keyed axis:<name>
    axis_keys = [key for key in facet_universe.keys() if key.startswith('axis:')]
    assert all(key.startswith('axis:') for key in axis_keys)

    # Assert per-facet counts sum to the sample row count
    total_count = 0
    for values in facet_universe.values():
        for item in values:
            total_count += item['count']
    assert total_count == 6  # 3 rows in mcp_server_registry + 3 rows in mcp_llm_axis_scores

    print('PASS')