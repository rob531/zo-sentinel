import unittest
import requests
from pattern_learner import learn_patterns

class TestPatternLearner(unittest.TestCase):
    def setUp(self):
        # Seed synthetic data using write_service
        self.test_data = {
            "patterns": [
                {"id": 1, "name": "pattern1", "data": [1, 2, 3]},
                {"id": 2, "name": "pattern2", "data": [4, 5, 6]},
            ]
        }
        response = requests.post("http://write_service/seed_data", json=self.test_data)
        self.assertEqual(response.status_code, 200)

    def test_learn_patterns(self):
        # Call the main learning function
        learned_patterns = learn_patterns()

        # Assert the output is non-empty
        self.assertTrue(learned_patterns)

        # Assert the learned patterns conform to expected structures
        for pattern in learned_patterns:
            self.assertIn("id", pattern)
            self.assertIn("name", pattern)
            self.assertIn("data", pattern)
            self.assertIsInstance(pattern["id"], int)
            self.assertIsInstance(pattern["name"], str)
            self.assertIsInstance(pattern["data"], list)

if __name__ == '__main__':
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
    print("PASS")