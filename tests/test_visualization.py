import json
import tempfile
import unittest
from pathlib import Path

from janq_lab.visualization.html_replay import (
    render_replay_html,
    render_replay_set_html,
    simulate_replay,
    simulate_replay_set,
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

    def test_simulated_replay_set_has_multiple_examples(self):
        replay_set = simulate_replay_set(
            seed=21,
            strategy="route_ev",
            examples=5,
            max_turns=2,
        )

        self.assertEqual(5, len(replay_set.replays))
        self.assertEqual([21, 22, 23, 24, 25], [replay.seed for replay in replay_set.replays])

    def test_render_html_contains_dashboard_and_probability_tables(self):
        replay_set = simulate_replay_set(
            seed=3,
            strategy="route_ev",
            examples=3,
            max_turns=2,
        )

        html = render_replay_set_html(replay_set)

        self.assertIn("当前策略：route_ev", html)
        self.assertIn("example-list", html)
        self.assertIn("prob-data", html)
        self.assertIn('data-area="4"', html)
        self.assertIn("区域概率", html)

    def test_render_single_replay_keeps_backwards_compatible_api(self):
        replay = simulate_replay(seed=3, strategy="route_ev", max_turns=2)

        html = render_replay_html(replay)

        self.assertIn("JanQ Offline Replay Dashboard", html)
        self.assertIn("摸到", html)
        self.assertIn("目标权重", html)

    def test_observed_source_uses_probe_log_starting_hands(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "janq_events.jsonl"
            events = [
                _event(1, "recv_game_haipai", {"status": "NORMAL", "haipai": list(range(1, 14))}),
                _event(2, "recv_game_haipai", {"status": "NORMAL", "haipai": list(range(2, 15))}),
            ]
            path.write_text("\n".join(events), encoding="utf-8")

            replay_set = simulate_replay_set(
                seed=1,
                strategy="public",
                examples=2,
                source="observed",
                events_path=path,
                max_turns=1,
            )

        self.assertEqual(2, replay_set.observed_hand_count)
        self.assertEqual(tuple(range(13)), replay_set.replays[0].initial_hand)
        self.assertEqual(tuple(range(1, 14)), replay_set.replays[1].initial_hand)

    def test_write_html_creates_parent_directory(self):
        replay = simulate_replay(seed=5, strategy="greedy", max_turns=1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "replay.html"
            written = write_replay_html(replay, path)

            self.assertTrue(written.exists())
            self.assertIn("<!doctype html>", written.read_text(encoding="utf-8"))


def _event(line_number, event_type, payload):
    return json.dumps(
        {
            "ts": f"2026-06-13T00:00:{line_number:02d}",
            "type": event_type,
            "payload": payload,
        }
    )


if __name__ == "__main__":
    unittest.main()
