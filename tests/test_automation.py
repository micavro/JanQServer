import json
import tempfile
import unittest
from pathlib import Path

from janq_lab.automation.bot import AutomationRunner
from janq_lab.automation.config import AutomationConfig, load_config
from janq_lab.automation.policy import StrategyPolicy
from janq_lab.automation.session_log import SessionLogger
from janq_lab.automation.state import BotGameState, reduce_event
from janq_lab.probe.events import parse_event


def event(line_number, event_type, payload):
    return parse_event(
        '{"ts":"2026-06-12T13:52:10+00:00","type":"%s","payload":%s}'
        % (event_type, payload),
        line_number=line_number,
    )


class AutomationTests(unittest.TestCase):
    def test_config_loads_simple_yaml_and_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "automation.yaml"
            path.write_text("mode: dry_run\nmax_hands: 12\nstrategy: public\n", encoding="utf-8")

            config = load_config(path, max_hands=20)

        self.assertEqual("dry_run", config.mode)
        self.assertEqual("public", config.strategy)
        self.assertEqual(20, config.max_hands)

    def test_reduce_api_events_to_actionable_states(self):
        state = BotGameState()
        state = reduce_event(
            state,
            event(
                1,
                "recv_game_haipai",
                '{"status":"NORMAL","zandan":8,"haipai":[1,2,3,4,5,6,7,8,9,28,29,30,31],"omoDora":5,"uraDora":6}',
            ),
        )
        self.assertEqual("shoot_wait", state.phase)
        self.assertEqual(13, len(state.hand))
        self.assertEqual("Normal", state.mode)

        state = reduce_event(
            state,
            event(
                2,
                "recv_game_tsumo",
                '{"status":"NORMAL","pai":32,"zandan":7,"richi":false,"tehai":[1,2,3,4,5,6,7,8,9,28,29,30,31,32]}',
            ),
        )
        self.assertEqual("user_wait", state.phase)
        self.assertEqual(14, len(state.hand))

    def test_policy_chooses_shot_and_discard(self):
        policy = StrategyPolicy("public")
        shot_state = BotGameState(
            phase="shoot_wait",
            mode="Normal",
            balls=8,
            hand=tuple(range(13)),
        )
        shot = policy.decide(shot_state)
        self.assertIsNotNone(shot.action)
        self.assertEqual("shot", shot.action.kind)
        self.assertIn(shot.action.area, range(1, 8))

        discard_state = BotGameState(
            phase="user_wait",
            mode="Normal",
            balls=7,
            hand=(0, 1, 2, 3, 4, 5, 6, 7, 8, 27, 28, 29, 30, 31),
        )
        discard = policy.decide(discard_state)
        self.assertIsNotNone(discard.action)
        self.assertIn(discard.action.kind, ("discard", "agari"))

    def test_dry_run_runner_logs_decision_and_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            config = AutomationConfig(
                mode="dry_run",
                session_log_path=str(session_path),
                session_dir=tmp,
                strategy="public",
                decision_cooldown_seconds=0,
            )
            runner = AutomationRunner(config, logger=SessionLogger(session_path))
            runner.process_event(
                event(
                    1,
                    "recv_game_haipai",
                    '{"status":"NORMAL","zandan":8,"haipai":[1,2,3,4,5,6,7,8,9,28,29,30,31]}',
                ),
                now=1.0,
            )
            rows = [
                json.loads(line)
                for line in session_path.read_text(encoding="utf-8").splitlines()
            ]

        types = [row["type"] for row in rows]
        self.assertIn("bot_state", types)
        self.assertIn("bot_decision", types)
        self.assertIn("bot_action_done", types)


if __name__ == "__main__":
    unittest.main()
