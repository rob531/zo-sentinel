from fastapi import Depends
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Organization, User
from app.dependencies import get_zo_computer_client
from app.schemas import BreakerActionResponse
from datetime import datetime
import requests

def reset_server_export_api_quarantine(db_session=Depends(get_session), zo_computer_client=Depends(get_zo_computer_client)):
    """
    Reset quarantine status for server_export_api.py by clearing related flags in the database.
    This does not rebuild the file but allows future rebuilds by removing quarantine blocks.
    """
    try:
        # Clear quarantine flags in MCPServerRegistry
        db_session.query(MCPServerRegistry).filter(
            MCPServerRegistry.file_name == 'server_export_api.py'
        ).update({'quarantined': False, 'quarantine_timestamp': None})

        # Log the reset action in MCPScoreDisputes
        reset_record = MCPScoreDisputes(
            file_name='server_export_api.py',
            action='reset_quarantine',
            timestamp=datetime.utcnow(),
            notes='Quarantine reset by breaker_action_reset_server_export_api.py'
        )
        db_session.add(reset_record)

        # Notify ZoComputer of the reset
        zo_computer_client.post(
            '/notify',
            json={
                'event': 'quarantine_reset',
                'file': 'server_export_api.py',
                'timestamp': datetime.utcnow().isoformat()
            }
        )

        db_session.commit()
        return BreakerActionResponse(
            success=True,
            message="Quarantine reset for server_export_api.py completed successfully"
        )
    except Exception as e:
        db_session.rollback()
        return BreakerActionResponse(
            success=False,
            message=f"Failed to reset quarantine: {str(e)}"
        )

if __name__ == "__main__":
    from app.db import get_session
    # FU-369: removed an import of `override_dependencies_for_testing` from a module that does
    # not exist in this tree, together with its call below. The call
    # installed nothing this file does not already do for itself.
    # FU-369: call removed with its phantom import (see above).

    # Test with in-memory SQLite for self-test
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine('sqlite:///:memory:')
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Mock ZoComputer client for testing
    class MockZoComputerClient:
        def post(self, url, json):
            return {'status': 'success'}

    app.dependency_overrides[get_zo_computer_client] = lambda: MockZoComputerClient()

    # Run test
    result = reset_server_export_api_quarantine()
    if result.success:
        print("PASS")
    else:
        print("FAIL")