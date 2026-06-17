import unittest

from janq_lab.analysis.economy_monte_carlo import _strategy_functions
from janq_lab.assets.nyukyu import load_tables
from janq_lab.automation.config import AutomationConfig
from janq_lab.strategy.review_regressions import REVIEW_REGRESSION_CASES
from janq_lab.strategy.route_ev2 import choose_route_ev2_area, choose_route_ev2_discard
from janq_lab.visualization.strategy_review_report import evaluate_strategy_review_cases


class RouteEv2StrategyTests(unittest.TestCase):
    def test_all_user_review_regression_cases_remain_guarded(self):
        results = evaluate_strategy_review_cases(strategy="route_ev2")

        self.assertEqual(len(REVIEW_REGRESSION_CASES), len(results))
        self.assertTrue(
            all(result.passed for result in results),
            [result.case.case_id for result in results if not result.passed],
        )

    def test_route_ev2_is_registered_for_analysis_and_automation(self):
        choose_area, choose_discard = _strategy_functions("route_ev2")
        config = AutomationConfig(strategy="route_ev2")

        config.validate()
        self.assertIs(choose_route_ev2_area, choose_area)
        self.assertIs(choose_route_ev2_discard, choose_discard)

    def test_route_ev2_keeps_locked_yakuman_route_before_shallow_ev_override(self):
        table = load_tables()["nyukyu_base_table.bytes"]

        decision = choose_route_ev2_area(
            (4, 5, 14, 18, 20, 22, 28, 29, 30, 31, 32, 33, 33),
            table,
            balls=8,
            dora_id=8,
            ura_dora_id=26,
        )

        self.assertEqual(4, decision.area)
        self.assertIn("route_ev2_keep_locked", decision.reason)
        self.assertIn("daisangen", decision.reason)

    def test_route_ev2_smoke_discards_within_interactive_budget_shape(self):
        decision = choose_route_ev2_discard(
            (0, 0, 1, 2, 3, 4, 7, 15, 19, 23, 26, 31, 32, 21),
            balls=7,
            drawn_tile=21,
            dora_id=19,
            ura_dora_id=12,
        )

        self.assertIn(decision.discard_tile, (31, 32))
        self.assertIn("route_ev2", decision.reason)


if __name__ == "__main__":
    unittest.main()
