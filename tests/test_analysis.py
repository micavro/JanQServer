import unittest
import json
import tempfile
from pathlib import Path

from janq_lab.analysis.monte_carlo import run_monte_carlo
from janq_lab.analysis.observed_ev import summarize_observed_ev
from janq_lab.analysis.stats import normal_mean_interval, wilson_interval


class AnalysisTests(unittest.TestCase):
    def test_wilson_interval_bounds(self):
        interval = wilson_interval(50, 100)
        self.assertLess(interval.low, 0.5)
        self.assertGreater(interval.high, 0.5)

    def test_normal_mean_interval_single_value(self):
        interval = normal_mean_interval([3.0])
        self.assertEqual(3.0, interval.low)
        self.assertEqual(3.0, interval.high)

    def test_monte_carlo_is_deterministic_for_seed(self):
        first = run_monte_carlo(hands=20, seed=7, strategy="public")
        second = run_monte_carlo(hands=20, seed=7, strategy="public")
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(20, first.hands)
        self.assertGreaterEqual(first.win_rate, 0.0)
        self.assertLessEqual(first.win_rate, 1.0)

    def test_observed_ev_from_replay_log(self):
        rows = [
            {"ts": "2026-06-12T13:52:10+00:00", "type": "recv_game_haipai", "payload": {"gold": 0, "mjchip": 100, "cchip": 0, "haipai": []}},
            {"ts": "2026-06-12T13:52:11+00:00", "type": "recv_janq_result", "payload": {"gold": 0, "mjchip": 115, "cchip": 0, "win": 1}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            summary = summarize_observed_ev(str(path), currency="mjchip")

        self.assertEqual(1, summary.hands)
        self.assertEqual(15.0, summary.mean_delta)


if __name__ == "__main__":
    unittest.main()
