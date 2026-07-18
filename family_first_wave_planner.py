from typing import List, Dict, Optional
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, Org, User
from fastapi import Depends
from sqlalchemy.orm import Session

def plan_first_wave(family_ids: List[str], config: Optional[Dict] = None) -> Dict[str, Dict]:
    if not isinstance(family_ids, list) or not all(isinstance(fid, str) for fid in family_ids):
        raise ValueError("family_ids must be a list of strings")

    if config is not None and not isinstance(config, dict):
        raise ValueError("config must be a dictionary or None")

    planning_details = {}

    for family_id in family_ids:
        # Default configuration for each family
        default_config = {
            'servers': ['server1', 'server2'],
            'axes': ['overall_risk', 'financial_risk', 'health_risk'],
            'thresholds': {
                'overall_risk': 0.7,
                'financial_risk': 0.6,
                'health_risk': 0.65
            }
        }

        # Apply overrides from config if provided
        if config and family_id in config:
            family_config = config[family_id]
            if 'servers' in family_config:
                default_config['servers'] = family_config['servers']
            if 'axes' in family_config:
                default_config['axes'] = family_config['axes']
            if 'thresholds' in family_config:
                default_config['thresholds'].update(family_config['thresholds'])

        planning_details[family_id] = default_config

    return planning_details

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from fastapi import FastAPI
    from app.models import Base

    app = FastAPI()
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    def get_test_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = get_test_session

    family_ids = ["famA", "famB"]
    result = plan_first_wave(family_ids)

    assert set(result.keys()) == set(family_ids)
    assert all('overall_risk' in details['axes'] for details in result.values())

    print('PASS')