import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from janq_lab.strategy.review_regressions import REVIEW_REGRESSION_CASES
from janq_lab.visualization.strategy_review_report import (
    evaluate_strategy_review_cases,
    render_strategy_review_html,
    write_strategy_review_html,
)


class StrategyReviewReportTests(unittest.TestCase):
    def test_all_shared_review_cases_pass(self):
        results = evaluate_strategy_review_cases()

        self.assertEqual(len(REVIEW_REGRESSION_CASES), len(results))
        self.assertTrue(
            all(result.passed for result in results),
            [result.case.case_id for result in results if not result.passed],
        )

    def test_html_renders_independent_states_and_constraints(self):
        results = evaluate_strategy_review_cases()

        html = render_strategy_review_html(results)

        self.assertEqual(len(results), html.count('class="case pass"'))
        self.assertIn("每个案例只包含决策当时的状态", html)
        self.assertIn("摸前手牌 13张", html)
        self.assertIn("禁止答案", html)
        self.assertIn("允许答案", html)
        self.assertIn("random-2-turn-9-discard", html)
        self.assertIn("第四张南必须直接摸切", html)
        self.assertIn('class="dora-strip"', html)
        self.assertIn("宝牌", html)
        self.assertIn("里宝牌", html)

    def test_html_renders_known_dora_as_tiles(self):
        case = replace(
            REVIEW_REGRESSION_CASES[0],
            dora_id=0,
            ura_dora_id=33,
        )
        results = evaluate_strategy_review_cases((case,))

        html = render_strategy_review_html(results)

        self.assertIn("tile-id-0", html)
        self.assertIn("tile-id-33", html)
        self.assertIn("1万", html)
        self.assertIn("中", html)

    def test_write_report_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "nested" / "strategy-tests.html"

            written = write_strategy_review_html(output)

            self.assertEqual(output, written)
            self.assertTrue(output.exists())
            self.assertIn(
                "JanQ 策略回归测试",
                output.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
