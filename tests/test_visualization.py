import json
import tempfile
import unittest
from pathlib import Path

from janq_lab.analysis.economy_monte_carlo import run_economy_monte_carlo
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

    def test_render_html_can_include_review_controls(self):
        replay_set = simulate_replay_set(
            seed=31,
            strategy="route_ev",
            examples=2,
            max_turns=1,
            include_bonus=False,
        )

        html = render_replay_set_html(replay_set, review_ui=True)

        self.assertIn('class="panel review-toolbar"', html)
        self.assertIn('id="promptOutput"', html)
        self.assertIn("initReviewUi", html)
        self.assertIn("janq-sim-review-v2", html)
        self.assertIn('data-shot-area="', html)
        self.assertIn('data-hand-before="', html)

    def test_bonus_replay_matches_economy_simulation(self):
        replay_set = simulate_replay_set(
            seed=91,
            strategy="public",
            examples=1,
            include_bonus=True,
            max_bonus_hands=20,
        )
        summary = run_economy_monte_carlo(
            sessions=1,
            seed=91,
            bet=10,
            strategy="public",
            max_bonus_hands=20,
        )
        replay = replay_set.replays[0]

        self.assertEqual(summary.total_payout, replay.total_payout)
        self.assertEqual(("paren", "paren"), tuple(hand.mode for hand in replay.bonus_hands))
        self.assertTrue(replay.bonus_hands[0].win)
        self.assertFalse(replay.bonus_hands[1].win)
        self.assertEqual(15, replay.bonus_hands[0].payout)

    def test_render_html_contains_bonus_chain_and_economy_summary(self):
        replay_set = simulate_replay_set(
            seed=91,
            strategy="public",
            examples=1,
            include_bonus=True,
            max_bonus_hands=20,
        )

        html = render_replay_set_html(replay_set)

        self.assertIn('class="panel economy-panel"', html)
        self.assertIn('class="bonus-chain"', html)
        self.assertIn("普通奖励游戏 #1", html)
        self.assertIn("完整游戏经济", html)
        self.assertIn("ROI", html)
        self.assertIn("RTP", html)

    def test_yakuman_bonus_replay_uses_progressive_payout(self):
        replay = simulate_replay(
            seed=1,
            strategy="route_ev",
            balls=8,
            initial_hand=(0, 0, 0, 1, 1, 1, 9, 9, 9, 18, 18, 18, 31),
            include_bonus=True,
            max_bonus_hands=10,
        )
        wins = [hand for hand in replay.bonus_hands if hand.win]

        self.assertTrue(replay.score.is_yakuman)
        self.assertEqual(("yakuman",) * 5, tuple(hand.mode for hand in replay.bonus_hands))
        self.assertEqual([200, 300, 400, 500], [hand.payout for hand in wins])
        self.assertEqual([2, 3, 4, 5], [hand.cumulative_yakuman_units for hand in wins])
        self.assertEqual(1500, replay.total_payout)

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
