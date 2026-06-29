import unittest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from signal_analyser.enrichment_harness import compute_score
from signal_analyser.models import McpSignalEnrichments, McpServerRegistry

class TestSignalEnrichmentWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Setup test database connection
        cls.engine = create_engine('sqlite:///:memory:')
        cls.Session = sessionmaker(bind=cls.engine)
        cls.session = cls.Session()

        # Create test tables
        McpSignalEnrichments.__table__.create(cls.engine)
        McpServerRegistry.__table__.create(cls.engine)

        # Insert test data
        test_servers = [McpServerRegistry(server_id=i) for i in range(1, 1329)]
        cls.session.add_all(test_servers)
        cls.session.commit()

    @classmethod
    def tearDownClass(cls):
        cls.session.close()
        cls.engine.dispose()

    @patch('signal_analyser.enrichment_harness.compute_score')
    def test_enrichment_wiring(self, mock_compute_score):
        # Setup mock return values for compute_score
        mock_compute_score.return_value = {
            'signal_type_1': 1000,
            'signal_type_2': 500,
            'signal_type_3': 300
        }

        # Call the function under test
        result = compute_score(self.session)

        # Verify the wiring
        self.assertEqual(len(result), 3)

        # Get server count from registry
        server_count = self.session.query(McpServerRegistry).count()
        self.assertEqual(server_count, 1328)

        # Check coverage for each signal type
        coverage_report = []
        for signal_type, count in result.items():
            coverage = (count / server_count) * 100
            coverage_report.append((signal_type, count, coverage))

            if coverage < 10:  # Example threshold for insufficient coverage
                self.fail(f"Insufficient coverage for {signal_type}: {coverage:.2f}%")

        # Print coverage report
        print("\nSignal Type Coverage Report:")
        print("{:<15} {:<10} {:<10}".format('Signal Type', 'Count', 'Coverage %'))
        for report in coverage_report:
            print("{:<15} {:<10} {:<10.2f}".format(*report))

if __name__ == '__main__':
    unittest.main()