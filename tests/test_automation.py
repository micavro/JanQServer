import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from janq_lab.automation.bankroll import choose_bet_tier, parse_bet_ladder
from janq_lab.automation.bot import AutomationRunner, ProbeTailer
from janq_lab.automation.config import AutomationConfig, load_config
from janq_lab.automation.executor import ExecutionResult, PluginExecutor
from janq_lab.automation.policy import BotAction, StrategyPolicy
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

    def test_config_accepts_plugin_live(self):
        config = AutomationConfig(mode="plugin_live", enter_janq_on_start=True)
        config.validate()
        self.assertTrue(config.enter_janq_on_start)

    def test_bankroll_policy_uses_200_100_hysteresis(self):
        ladder = parse_bet_ladder("10,20,30,50,100,200")

        self.assertEqual(
            10,
            choose_bet_tier(3999, ladder, current_bet=10).bet,
        )
        self.assertEqual(
            20,
            choose_bet_tier(4000, ladder, current_bet=10).bet,
        )
        self.assertEqual(
            20,
            choose_bet_tier(2500, ladder, current_bet=20).bet,
        )
        self.assertEqual(
            10,
            choose_bet_tier(1999, ladder, current_bet=20).bet,
        )

    def test_runner_writes_bridge_bet_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = Path(tmp) / "bridge"
            session_path = Path(tmp) / "session.jsonl"
            config = AutomationConfig(
                mode="dry_run",
                bridge_dir=str(bridge),
                session_log_path=str(session_path),
                forced_bet=20,
            )
            runner = AutomationRunner(config, logger=SessionLogger(session_path))
            runner._update_bet_target("test")
            settings = json.loads((bridge / "settings.json").read_text(encoding="utf-8"))

        self.assertEqual(20, settings["targetBet"])
        self.assertEqual([10, 20, 30, 50, 100, 200], settings["betLadder"])

    def test_probe_tailer_can_start_at_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text(
                '{"ts":"2026-06-12T13:52:10+00:00","type":"old","payload":{}}\n',
                encoding="utf-8",
            )
            tailer = ProbeTailer(path, start_at_end=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    '{"ts":"2026-06-12T13:52:11+00:00","type":"new","payload":{}}\n'
                )

            events = list(tailer.read_new_events())

        self.assertEqual(["new"], [item.type for item in events])

    def test_plugin_runner_reselects_stale_bet_before_betting(self):
        class FakeExecutor:
            def __init__(self):
                self.actions = []

            def execute(self, action, rng=None):
                del rng
                self.actions.append(action)
                return ExecutionResult(True, "plugin_live", action.to_dict(), {})

        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            config = AutomationConfig(
                mode="plugin_live",
                session_log_path=str(session_path),
                session_dir=tmp,
                decision_cooldown_seconds=0,
            )
            runner = AutomationRunner(config, logger=SessionLogger(session_path))
            fake = FakeExecutor()
            runner.executor = fake
            runner.state = BotGameState(phase="bet_wait", main_button="Bet")
            runner.target_bet = 10
            runner.selected_bet = 20
            runner.selected_bet_target = 20

            runner._maybe_decide(1.0)

        self.assertTrue(runner.running)
        self.assertEqual(["reselect_bet"], [action.kind for action in fake.actions])

    def test_plugin_runner_accepts_current_game_bet_from_snapshot(self):
        class FakeExecutor:
            def __init__(self):
                self.actions = []

            def execute(self, action, rng=None):
                del rng
                self.actions.append(action)
                return ExecutionResult(True, "plugin_live", action.to_dict(), {})

        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            config = AutomationConfig(
                mode="plugin_live",
                session_log_path=str(session_path),
                session_dir=tmp,
                decision_cooldown_seconds=0,
            )
            runner = AutomationRunner(config, logger=SessionLogger(session_path))
            fake = FakeExecutor()
            runner.executor = fake
            runner.target_bet = 10

            runner.process_event(
                event(
                    1,
                    "game_state_snapshot",
                    '{"gameMode":"Normal","state":"BetWait","requestState":"BetWait",'
                    '"mainButtonType":"Bet","mainButtonRequest":"Bet","betRate":10,'
                    '"pais":[1,2,3]}',
                ),
                now=1.0,
            )

        self.assertTrue(runner.running)
        self.assertEqual(["press_main"], [action.kind for action in fake.actions])

    def test_plugin_runner_pauses_when_current_target_falls_back(self):
        class FakeExecutor:
            def __init__(self):
                self.actions = []

            def execute(self, action, rng=None):
                del rng
                self.actions.append(action)
                return ExecutionResult(True, "plugin_live", action.to_dict(), {})

        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            config = AutomationConfig(
                mode="plugin_live",
                session_log_path=str(session_path),
                session_dir=tmp,
                decision_cooldown_seconds=0,
            )
            runner = AutomationRunner(config, logger=SessionLogger(session_path))
            fake = FakeExecutor()
            runner.executor = fake
            runner.state = BotGameState(phase="bet_wait", main_button="Bet")
            runner.target_bet = 20
            runner.selected_bet = 10
            runner.selected_bet_target = 20
            runner.selected_bet_mode = "highest_below_target"

            runner._maybe_decide(1.0)

            rows = [
                json.loads(line)
                for line in session_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertFalse(runner.running)
        self.assertEqual([], fake.actions)
        self.assertEqual("bet_target_unavailable_or_fallback", rows[-1]["payload"]["reason"])

    def test_plugin_runner_pauses_on_repeated_login_dialog(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            config = AutomationConfig(
                mode="plugin_live",
                session_log_path=str(session_path),
                session_dir=tmp,
                decision_cooldown_seconds=0,
            )
            runner = AutomationRunner(config, logger=SessionLogger(session_path))

            runner.process_event(
                event(
                    1,
                    "janq_navigation_login_blocked",
                    '{"sequence":"Login.LoginErrorSequence","dismissCount":3,'
                    '"reason":"repeated_login_dialog",'
                    '"dialogReason":"account_conflict_or_login_error"}',
                ),
                now=1.0,
            )

            rows = [
                json.loads(line)
                for line in session_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertFalse(runner.running)
        self.assertEqual("login_blocked_or_repeated_dialog", rows[-1]["payload"]["reason"])
        self.assertEqual(
            "account_conflict_or_login_error",
            rows[-1]["payload"]["probe_payload"]["dialogReason"],
        )

    def test_plugin_runner_pauses_on_runtime_login_dialog(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            config = AutomationConfig(
                mode="plugin_live",
                session_log_path=str(session_path),
                session_dir=tmp,
                decision_cooldown_seconds=0,
            )
            runner = AutomationRunner(config, logger=SessionLogger(session_path))

            runner.process_event(
                event(
                    1,
                    "janq_runtime_login_blocked",
                    '{"sequence":"Login.LoginErrorSequence",'
                    '"reason":"account_conflict_or_login_error",'
                    '"dialogReason":"account_conflict_or_login_error"}',
                ),
                now=1.0,
            )

            rows = [
                json.loads(line)
                for line in session_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertFalse(runner.running)
        self.assertEqual("login_blocked_or_repeated_dialog", rows[-1]["payload"]["reason"])
        self.assertEqual(
            "account_conflict_or_login_error",
            rows[-1]["payload"]["probe_payload"]["dialogReason"],
        )

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
        self.assertEqual("wait", state.phase)
        state = reduce_event(
            state,
            event(
                3,
                "game_state_snapshot",
                '{"gameMode":"Normal","state":"UserWait","requestState":"UserWait",'
                '"mainButtonType":"Shot","mainButtonRequest":"Shot",'
                '"pais":[0,1,2,3,4,5,6,7,8,27,28,29,30,31]}',
            ),
        )
        self.assertEqual("user_wait", state.phase)
        self.assertEqual(14, len(state.hand))

    def test_probe_loaded_resets_state_from_prior_process(self):
        state = BotGameState(
            phase="bet_wait",
            balls=1,
            hand=tuple(range(14)),
            completed_hands=3,
        )
        state = reduce_event(state, event(10, "probe_loaded", '{"version":"0.2.0"}'))

        self.assertEqual("unknown", state.phase)
        self.assertEqual(0, state.completed_hands)
        self.assertEqual(10, state.last_line)

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

    def test_snapshot_uses_requested_button_during_transition(self):
        state = reduce_event(
            BotGameState(),
            event(
                1,
                "game_state_snapshot",
                '{"state":"BetWait","requestState":"BetWait",'
                '"mainButtonType":"Shot","mainButtonRequest":"Bet",'
                '"pais":[31,31,31,32,32,32,33,33,4,5,6,2,3,14]}',
            ),
        )

        self.assertEqual("Bet", state.main_button)
        self.assertEqual("bet_wait", state.phase)

    def test_start_check_interrupt_snapshot_is_not_actionable(self):
        state = reduce_event(
            BotGameState(),
            event(
                1,
                "game_state_snapshot",
                '{"gameMode":"Start","state":"None","requestState":"CheckInterrupt",'
                '"mainButtonType":"None","mainButtonRequest":"None",'
                '"pais":[31,31,31,32,32,32,33,33,4,5,6,2,3,14]}',
            ),
        )

        self.assertEqual("wait", state.phase)

    def test_retry_snapshot_is_not_actionable_even_with_shot_button(self):
        state = reduce_event(
            BotGameState(),
            event(
                1,
                "game_state_snapshot",
                '{"gameMode":"Normal","state":"RetryRun","requestState":"RetryRun",'
                '"mainButtonType":"Shot","mainButtonRequest":"Shot",'
                '"pais":[3,3,3,13,15,16,16,16,17,29,29,29,29,9999]}',
            ),
        )

        self.assertEqual("wait", state.phase)

    def test_bet_wait_overrides_unconfirmed_user_wait(self):
        state = reduce_event(
            BotGameState(phase="user_wait", hand=tuple(range(14))),
            event(
                1,
                "game_state_snapshot",
                '{"gameMode":"Normal","state":"BetWait","mainButtonRequest":"Bet",'
                '"pais":[31,31,31,32,32,32,33,33,4,5,6,2,3,14]}',
            ),
        )

        self.assertEqual("bet_wait", state.phase)

    def test_explicit_none_button_clears_stale_agari_during_result_animation(self):
        state = reduce_event(
            BotGameState(
                phase="result",
                mode="ParenChallenge",
                status="PARENCHAN",
                main_button="Agari",
                hand=tuple(range(14)),
            ),
            event(
                1,
                "game_state_snapshot",
                '{"gameMode":"Normal","gameModeNext":"ParenChallenge",'
                '"state":"AgariRun","requestState":"Result",'
                '"mainButtonType":"None","mainButtonRequest":"None",'
                '"pais":[0,1,2,3,4,5,6,7,8,9,10,11,12,13]}',
            ),
        )

        self.assertEqual("wait", state.phase)
        self.assertIsNone(state.main_button)

    def test_replay_tsumo_waits_for_retry_to_finish(self):
        state = reduce_event(
            BotGameState(phase="shot_sent", hand=(3, 3, 3, 13, 15, 16, 16, 16, 17, 29, 29, 29, 29)),
            event(
                1,
                "recv_game_tsumo",
                '{"status":"NORMAL","pai":30,"zandan":3,"richi":false,"replay":true,'
                '"agari":false,"tehai":[4,4,4,14,16,17,17,17,18,30,30,30,30]}',
            ),
        )

        self.assertEqual("wait", state.phase)

        state = reduce_event(
            state,
            event(
                2,
                "game_state_snapshot",
                '{"gameMode":"Normal","state":"ShootWait","requestState":"ShootWait",'
                '"mainButtonType":"None","mainButtonRequest":"Shot",'
                '"pais":[3,3,3,13,15,16,16,16,17,29,29,29,29,9999]}',
            ),
        )

        self.assertEqual("shoot_wait", state.phase)

    def test_policy_waits_on_invalid_transition_hand(self):
        policy = StrategyPolicy("public")
        decision = policy.decide(
            BotGameState(
                phase="user_wait",
                mode="Normal",
                balls=2,
                hand=(3, 3, 3, 13, 15, 16, 16, 16, 17, 29, 29, 29, 29, 29),
            )
        )

        self.assertIsNone(decision.action)
        self.assertEqual("user_wait_invalid_hand", decision.reason)

    def test_snapshot_does_not_reopen_action_after_send(self):
        state = reduce_event(
            BotGameState(phase="shoot_wait"),
            event(1, "send_action_shot", '{"area":2}'),
        )
        state = reduce_event(
            state,
            event(
                2,
                "game_state_snapshot",
                '{"state":"ShootWait","mainButtonType":"Shot","pais":[1,2,3]}',
            ),
        )

        self.assertEqual("shot_sent", state.phase)

    def test_snapshot_does_not_hide_confirmed_user_wait(self):
        state = reduce_event(
            BotGameState(phase="shot_sent"),
            event(
                1,
                "recv_game_tsumo",
                '{"status":"NORMAL","pai":31,"zandan":7,"agari":false,'
                '"tehai":[1,2,3,4,5,6,7,8,9,28,29,30,31,32]}',
            ),
        )
        state = reduce_event(
            state,
            event(
                2,
                "game_state_snapshot",
                '{"state":"UserWait","mainButtonType":"Shot","pais":[0,1,2,3,4,5,6,7,8,27,28,29,30,31]}',
            ),
        )
        state = reduce_event(
            state,
            event(
                3,
                "game_state_snapshot",
                '{"state":"ShootRun","mainButtonType":"Shot","pais":[1,2,3]}',
            ),
        )

        self.assertEqual("user_wait", state.phase)

    def test_one_ball_tsumo_waits_for_user_wait_snapshot(self):
        state = reduce_event(
            BotGameState(phase="shot_sent"),
            event(
                1,
                "recv_game_tsumo",
                '{"status":"NORMAL","pai":31,"zandan":1,"agari":false,'
                '"tehai":[1,2,3,4,5,6,7,8,9,28,29,30,31,32]}',
            ),
        )

        self.assertEqual("wait", state.phase)

    def test_one_ball_remaining_still_allows_discard_after_user_wait(self):
        state = reduce_event(
            BotGameState(phase="shot_sent"),
            event(
                1,
                "recv_game_tsumo",
                '{"status":"NORMAL","pai":31,"zandan":1,"agari":false,'
                '"tehai":[1,2,3,4,5,6,7,8,9,28,29,30,31,32]}',
            ),
        )
        state = reduce_event(
            state,
            event(
                2,
                "game_state_snapshot",
                '{"gameMode":"Normal","state":"UserWait","requestState":"UserWait",'
                '"mainButtonType":"Shot","mainButtonRequest":"Shot",'
                '"pais":[0,1,2,3,4,5,6,7,8,27,28,29,30,31]}',
            ),
        )

        self.assertEqual("user_wait", state.phase)

    def test_no_balls_remaining_waits_for_result(self):
        state = reduce_event(
            BotGameState(phase="shot_sent"),
            event(
                1,
                "recv_game_tsumo",
                '{"status":"NORMAL","pai":31,"zandan":0,"agari":false,'
                '"tehai":[1,2,3,4,5,6,7,8,9,28,29,30,31,32]}',
            ),
        )

        self.assertEqual("resolving", state.phase)

        state = reduce_event(state, event(2, "send_ryukyoku", "{}"))
        self.assertEqual("result", state.phase)
        self.assertEqual(1, state.completed_hands)
        self.assertEqual("ryukyoku", state.last_result["type"])

        state = reduce_event(
            state,
            event(
                3,
                "game_state_snapshot",
                '{"state":"BetWait","mainButtonType":"Bet","pais":[1,2,3]}',
            ),
        )
        self.assertEqual("bet_wait", state.phase)
        self.assertEqual(1, state.completed_hands)

    def test_result_after_draw_does_not_double_count_hand(self):
        state = reduce_event(
            BotGameState(phase="resolving"),
            event(1, "send_ryukyoku", "{}"),
        )
        state = reduce_event(
            state,
            event(
                2,
                "recv_janq_result",
                '{"status":"NORMAL","mjchip":1000,"tehai":[1,2,3]}',
            ),
        )

        self.assertEqual(1, state.completed_hands)

    def test_runner_stops_immediately_on_draw_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            config = AutomationConfig(
                mode="dry_run",
                session_log_path=str(session_path),
                session_dir=tmp,
                max_hands=1,
                decision_cooldown_seconds=0,
            )
            runner = AutomationRunner(config, logger=SessionLogger(session_path))
            runner.process_event(event(1, "send_ryukyoku", "{}"), now=1.0)

        self.assertFalse(runner.running)
        self.assertEqual(1, runner.state.completed_hands)

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

    def test_runner_allows_same_shot_after_replay_retry(self):
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
            shoot = event(
                1,
                "game_state_snapshot",
                '{"gameMode":"Normal","state":"ShootWait","requestState":"ShootWait",'
                '"mainButtonRequest":"Shot","balls":2,'
                '"pais":[0,1,2,3,4,5,6,7,8,9,10,11,12,9999]}',
            )
            replay = event(
                2,
                "recv_game_tsumo",
                '{"status":"NORMAL","pai":30,"zandan":2,"richi":false,"replay":true,'
                '"agari":false,"tehai":[1,2,3,4,5,6,7,8,9,10,11,12,13]}',
            )
            retry_ready = event(
                3,
                "game_state_snapshot",
                '{"gameMode":"Normal","state":"ShootWait","requestState":"ShootWait",'
                '"mainButtonRequest":"Shot","balls":2,'
                '"pais":[0,1,2,3,4,5,6,7,8,9,10,11,12,9999]}',
            )

            runner.process_event(shoot, now=1.0)
            runner.process_event(replay, now=2.0)
            runner.process_event(retry_ready, now=3.0)
            rows = [
                json.loads(line)
                for line in session_path.read_text(encoding="utf-8").splitlines()
            ]

        decisions = [row for row in rows if row["type"] == "bot_decision"]
        self.assertEqual(2, len(decisions))

    def test_plugin_runner_pauses_on_shot_confirmation_area_mismatch(self):
        class FakeExecutor:
            def execute(self, action, rng=None):
                del rng
                return ExecutionResult(True, "plugin_live", action.to_dict(), {})

        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            config = AutomationConfig(
                mode="plugin_live",
                session_log_path=str(session_path),
                session_dir=tmp,
                strategy="public",
                decision_cooldown_seconds=0,
            )
            runner = AutomationRunner(config, logger=SessionLogger(session_path))
            runner.executor = FakeExecutor()
            runner.process_event(
                event(
                    1,
                    "recv_game_haipai",
                    '{"status":"NORMAL","zandan":8,'
                    '"haipai":[1,2,3,4,5,6,7,8,9,28,29,30,31]}',
                ),
                now=1.0,
            )
            self.assertIsNotNone(runner.pending)
            requested = runner.pending.action.area
            wrong_area = 5 if requested != 5 else 4

            runner.process_event(
                event(2, "send_action_shot", '{"area":%d}' % wrong_area),
                now=2.0,
            )

            rows = [
                json.loads(line)
                for line in session_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertFalse(runner.running)
        self.assertEqual(
            "confirmation_payload_mismatch:send_action_shot",
            rows[-1]["payload"]["reason"],
        )
        self.assertIn("shot_area", rows[-1]["payload"]["mismatch"])

    def test_plugin_executor_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = AutomationConfig(
                mode="plugin_live",
                bridge_dir=tmp,
                action_delay_min_seconds=0,
                action_delay_max_seconds=0,
                bridge_result_timeout_seconds=2,
            )
            executor = PluginExecutor(config)
            commands = Path(tmp) / "commands"
            results = Path(tmp) / "results"

            def fake_plugin():
                deadline = time.monotonic() + 1
                command_path = None
                while time.monotonic() < deadline:
                    matches = list(commands.glob("*.json")) if commands.exists() else []
                    if matches:
                        command_path = matches[0]
                        break
                    time.sleep(0.01)
                self.assertIsNotNone(command_path)
                command = json.loads(command_path.read_text(encoding="utf-8"))
                results.mkdir(parents=True, exist_ok=True)
                (results / f"{command['id']}.json").write_text(
                    json.dumps(
                        {
                            "id": command["id"],
                            "kind": command["kind"],
                            "success": True,
                            "error": None,
                        }
                    ),
                    encoding="utf-8",
                )

            thread = threading.Thread(target=fake_plugin)
            thread.start()
            result = executor.execute(BotAction("shot", area=4))
            thread.join(timeout=2)

        self.assertTrue(result.success)
        self.assertEqual("plugin_live", result.mode)
        self.assertEqual("shot", result.details["bridge_result"]["kind"])


if __name__ == "__main__":
    unittest.main()
