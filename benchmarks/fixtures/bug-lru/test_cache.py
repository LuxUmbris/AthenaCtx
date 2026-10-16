import unittest

from gate import Gate


class GateTests(unittest.TestCase):
    def test_false_hit_refreshes_lru_position(self):
        gate = Gate({("a", "read"): False, ("b", "read"): True, ("c", "read"): True})
        gate.allowed("a", "read")
        gate.allowed("b", "read")
        gate.allowed("a", "read")
        gate.allowed("c", "read")
        gate.allowed("a", "read")
        self.assertEqual(gate.reads, 3)


if __name__ == "__main__":
    unittest.main()
