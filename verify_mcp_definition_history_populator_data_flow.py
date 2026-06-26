import unittest
from datetime import datetime, timedelta
from write_service import WriteService

class TestMCPDefinitionHistoryPopulator(unittest.TestCase):
    def setUp(self):
        self.write_service = WriteService()
        self.table_name = 'mcp_definition_history'

    def test_recent_entries_exist(self):
        # Check if there are entries from the last 24 hours
        one_day_ago = datetime.utcnow() - timedelta(days=1)
        query = f"""
            SELECT COUNT(*)
            FROM {self.table_name}
            WHERE created_at >= %s
        """
        count = self.write_service.query(query, (one_day_ago,))[0][0]
        self.assertGreater(count, 0, "No recent entries found in mcp_definition_history")

    def test_data_consistency(self):
        # Check if required fields are populated
        query = f"""
            SELECT COUNT(*)
            FROM {self.table_name}
            WHERE mcp_id IS NULL OR definition IS NULL OR created_at IS NULL
        """
        count = self.write_service.query(query)[0][0]
        self.assertEqual(count, 0, "Some entries have NULL values in required fields")

    def test_source_consistency(self):
        # Check if data matches source (mcp_submissions)
        query = """
            SELECT COUNT(*)
            FROM mcp_definition_history mdh
            JOIN mcp_submissions ms ON mdh.mcp_id = ms.id
            WHERE mdh.definition != ms.definition
        """
        count = self.write_service.query(query)[0][0]
        self.assertEqual(count, 0, "Data inconsistency between mcp_definition_history and mcp_submissions")

if __name__ == '__main__':
    unittest.main()