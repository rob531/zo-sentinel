import time
import unittest
from write_service import WriteService
from service_health import ServiceHealth

class TestGateScheduler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.write_service = WriteService()
        cls.service_health = ServiceHealth()
        cls.gate_scheduler_healthy = cls.service_health.check_service('gate_scheduler')

    def test_gate_scheduling_functionality(self):
        if not self.gate_scheduler_healthy:
            self.fail("Gate scheduler is not healthy")

        # Insert dummy gate entries
        test_gates = [
            {'gate_id': 'test_gate_1', 'scheduled_time': time.time() + 5, 'status': 'scheduled'},
            {'gate_id': 'test_gate_2', 'scheduled_time': time.time() + 10, 'status': 'scheduled'}
        ]

        for gate in test_gates:
            self.write_service.insert_gate(gate['gate_id'], gate['scheduled_time'], gate['status'])

        # Wait for gate scheduler to process the gates
        time.sleep(15)

        # Verify gate status updates
        for gate in test_gates:
            gate_status = self.write_service.get_gate_status(gate['gate_id'])
            self.assertEqual(gate_status, 'completed', f"Gate {gate['gate_id']} did not complete as expected")

if __name__ == '__main__':
    unittest.main()
    print('PASS')