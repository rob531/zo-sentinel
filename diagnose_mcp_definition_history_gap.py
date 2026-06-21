import logging
import sqlite3
from typing import List, Dict, Optional

class MCPDefinitionHistoryAnalyzer:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.logger = logging.getLogger(__name__)

    def check_upstream_data_sources(self) -> Dict[str, bool]:
        """Check if upstream data sources exist and are accessible."""
        sources = {
            'mcp_definitions': False,
            'pipeline_logs': False,
            'raw_data': False
        }

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Check mcp_definitions table
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mcp_definitions'")
                if cursor.fetchone():
                    sources['mcp_definitions'] = True

                # Check pipeline_logs table
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_logs'")
                if cursor.fetchone():
                    sources['pipeline_logs'] = True

                # Check raw_data table (assuming it's the source)
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='raw_data'")
                if cursor.fetchone():
                    sources['raw_data'] = True

        except sqlite3.Error as e:
            self.logger.error(f"Database error: {e}")

        return sources

    def check_pipeline_steps(self) -> List[str]:
        """Check if all pipeline steps have been executed successfully."""
        missing_steps = []

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Check if the ETL step was completed
                cursor.execute("SELECT COUNT(*) FROM pipeline_logs WHERE step='ETL' AND status='completed'")
                if cursor.fetchone()[0] == 0:
                    missing_steps.append("ETL step not completed")

                # Check if the transformation step was completed
                cursor.execute("SELECT COUNT(*) FROM pipeline_logs WHERE step='transformation' AND status='completed'")
                if cursor.fetchone()[0] == 0:
                    missing_steps.append("Transformation step not completed")

                # Check if the load step was completed
                cursor.execute("SELECT COUNT(*) FROM pipeline_logs WHERE step='load' AND status='completed'")
                if cursor.fetchone()[0] == 0:
                    missing_steps.append("Load step not completed")

        except sqlite3.Error as e:
            self.logger.error(f"Database error: {e}")

        return missing_steps

    def analyze_gap(self) -> Dict[str, Optional[str]]:
        """Analyze the gap in mcp_definition_history and suggest potential causes."""
        analysis = {
            'upstream_sources': None,
            'missing_pipeline_steps': None,
            'potential_causes': None
        }

        # Check upstream data sources
        analysis['upstream_sources'] = self.check_upstream_data_sources()

        # Check pipeline steps
        analysis['missing_pipeline_steps'] = self.check_pipeline_steps()

        # Suggest potential causes
        causes = []
        if not analysis['upstream_sources']['mcp_definitions']:
            causes.append("mcp_definitions table is missing or empty")
        if not analysis['upstream_sources']['pipeline_logs']:
            causes.append("pipeline_logs table is missing or empty")
        if not analysis['upstream_sources']['raw_data']:
            causes.append("raw_data table is missing or empty")
        if analysis['missing_pipeline_steps']:
            causes.append(f"Pipeline steps missing: {', '.join(analysis['missing_pipeline_steps'])}")

        analysis['potential_causes'] = causes if causes else ["Unknown cause"]

        return analysis

def self_test():
    """Self-test for MCPDefinitionHistoryAnalyzer."""
    import tempfile
    import os

    # Create a temporary database for testing
    db_fd, db_path = tempfile.mkstemp()
    os.close(db_fd)

    # Initialize the database with some test tables
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        # Create raw_data table
        cursor.execute("""
        CREATE TABLE raw_data (
            id INTEGER PRIMARY KEY,
            data TEXT
        )
        """)

        # Create pipeline_logs table with missing steps
        cursor.execute("""
        CREATE TABLE pipeline_logs (
            id INTEGER PRIMARY KEY,
            step TEXT,
            status TEXT
        )
        """)
        cursor.execute("INSERT INTO pipeline_logs (step, status) VALUES ('ETL', 'completed')")

        # mcp_definitions table is missing

    # Test the analyzer
    analyzer = MCPDefinitionHistoryAnalyzer(db_path)
    analysis = analyzer.analyze_gap()

    # Assertions
    assert not analysis['upstream_sources']['mcp_definitions']
    assert analysis['upstream_sources']['pipeline_logs']
    assert analysis['upstream_sources']['raw_data']
    assert len(analysis['missing_pipeline_steps']) == 2  # transformation and load steps are missing
    assert "mcp_definitions table is missing or empty" in analysis['potential_causes']
    assert "Pipeline steps missing" in analysis['potential_causes'][1]

    # Clean up
    os.remove(db_path)

    print("Self-test passed!")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    self_test()