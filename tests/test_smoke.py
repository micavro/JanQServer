import unittest

from janq_lab.assets.nyukyu import load_tables
from janq_lab.automation.policy import StrategyPolicy


class SmokeTests(unittest.TestCase):
    def test_tables_and_default_policy_load(self) -> None:
        tables = load_tables()
        self.assertIn("nyukyu_base_table.bytes", tables)
        self.assertEqual(len(tables["nyukyu_base_table.bytes"].areas), 7)
        self.assertEqual(StrategyPolicy("route_ev").strategy, "route_ev")


if __name__ == "__main__":
    unittest.main()
