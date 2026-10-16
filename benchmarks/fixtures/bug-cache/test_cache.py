import unittest

from gate import Gate


class GateTests(unittest.TestCase):
    def test_false_decision_is_cached(self):
        gate = Gate({("a", "read"): False})
        self.assertFalse(gate.allowed("a", "read"))
        self.assertFalse(gate.allowed("a", "read"))
        self.assertEqual(gate.reads, 1)


if __name__ == "__main__":
    unittest.main()
