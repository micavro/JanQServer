import tempfile
import unittest
from pathlib import Path

from janq_lab.visualization.html_replay import (
    render_replay_html,
    simulate_replay,
    write_replay_html,
)


class HtmlReplayTests(unittest.TestCase):
    def test_simulated_replay_is_deterministic(self):
        first = simulate_replay(seed=11, strategy="public", max_turns=4)
        second = simulate_replay(seed=11, strategy="public", max_turns=4)

        self.assertEqual(first.initial_hand, second.initial_hand)
        self.assertEqual(first.final_hand, second.final_hand)
        self.assertEqual(
            [turn.drawn_tile for turn in first.turns],
            [turn.drawn_tile for turn in second.turns],
        )

    def test_render_html_contains_turn_details(self):
        replay = simulate_replay(seed=3, strategy="route_ev", max_turns=2)

        html = render_replay_html(replay)

        self.assertIn("当前策略：route_ev", html)
        self.assertIn("摸到", html)
        self.assertIn("区域理由", html)
        self.assertIn("回合后", html)

    def test_write_html_creates_parent_directory(self):
        replay = simulate_replay(seed=5, strategy="greedy", max_turns=1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "replay.html"
            written = write_replay_html(replay, path)

            self.assertTrue(written.exists())
            self.assertIn("<!doctype html>", written.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
