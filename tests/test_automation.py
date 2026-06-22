import argparse
import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

from janq_lab.automation.accounts import select_account, update_account_result
from janq_lab.automation.bankroll import choose_bet_tier, parse_bet_ladder
from janq_lab.automation.bot import AutomationRunner, ProbeTailer
from janq_lab.automation.config import AutomationConfig, load_config
from janq_lab.automation.executor import ExecutionResult, PluginExecutor
from janq_lab.automation.policy import BotAction, StrategyPolicy
from janq_lab.automation.session_log import SessionLogger
from janq_lab.automation.state import BotGameState, CurrencyState, reduce_event
from janq_lab.probe.events import parse_event
from scripts import run_account_batch as account_batch_script
from scripts import run_account_prep as account_prep_script
from scripts import run_register_janq_loop as register_loop_script
from scripts.run_account_batch import classify_status, send_bridge_command, summarize_session
from scripts.run_register_janq_loop import (
    cleanup_bridge_working_files as cleanup_register_bridge_files,
    exception_text as register_exception_text,
    load_loop_resume_state,
    mark_account_after_loop_error,
    send_bridge_command as send_register_bridge_command,
    should_preserve_prep_state,
    should_attempt_exit_to_login,
)


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

    def test_cli_scripts_can_rebind_workspace_root(self):
        original_env_workspace = os.environ.get("JANQ_WORKSPACE")
        original_env_log = os.environ.get("JANQ_PROBE_LOG")
        original_register_root = register_loop_script.ROOT
        original_prep_root = account_prep_script.ROOT
        original_batch_root = account_batch_script.ROOT
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = (Path(tmp) / "JanQ2").resolve()

                register_loop_script.configure_root(root)
                account_prep_script.configure_root(root)
                account_batch_script.configure_root(root)

                self.assertEqual(root, register_loop_script.ROOT)
                self.assertEqual(root / "_runtime" / "bridge", register_loop_script.BRIDGE_DIR)
                self.assertEqual(root / "_runtime" / "logs" / "janq_events.jsonl", register_loop_script.EVENTS_PATH)
                self.assertEqual(root / "_runtime" / "account_prep", account_prep_script.RUNTIME)
                self.assertEqual(root / "src", account_batch_script.SRC)
                self.assertEqual(str(root), os.environ["JANQ_WORKSPACE"])
        finally:
            register_loop_script.configure_root(original_register_root)
            account_prep_script.configure_root(original_prep_root)
            account_batch_script.configure_root(original_batch_root)
            if original_env_workspace is None:
                os.environ.pop("JANQ_WORKSPACE", None)
            else:
                os.environ["JANQ_WORKSPACE"] = original_env_workspace
            if original_env_log is None:
                os.environ.pop("JANQ_PROBE_LOG", None)
            else:
                os.environ["JANQ_PROBE_LOG"] = original_env_log

    def test_account_store_selects_account_without_exposing_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accounts.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "requestId": "req-a",
                            "loginId": "mja12345678",
                            "password": "secret-pass",
                            "nickname": "JQreqa",
                            "finalMjchip": 850,
                            "status": "complete",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            account = select_account(path, "req-a")

        self.assertEqual("req-a", account.request_id)
        self.assertEqual("mja***", account.masked_login_id)
        self.assertNotIn("password", account.public_payload())

    def test_account_store_updates_result_without_touching_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accounts.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "requestId": "req-a",
                            "loginId": "mja12345678",
                            "password": "secret-pass",
                            "nickname": "JQreqa",
                            "finalMjchip": 850,
                            "status": "complete",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            payload = update_account_result(
                path,
                "req-a",
                current_mjchip=7,
                status="bankrupt",
                terminal_reason="bankruptcy_mjchip",
                session_path="session.jsonl",
                completed_hands=12,
            )
            rows = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual("bankrupt", rows[0]["status"])
        self.assertEqual(7, rows[0]["currentMjchip"])
        self.assertEqual(7, rows[0]["finalMjchip"])
        self.assertEqual("secret-pass", rows[0]["password"])
        self.assertEqual("mja***", payload["login_id"])

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

    def test_plugin_runner_accepts_press_main_bridge_result_confirmation(self):
        class FakeExecutor:
            def __init__(self):
                self.actions = []

            def execute(self, action, rng=None):
                del rng
                self.actions.append(action)
                return ExecutionResult(
                    True,
                    "plugin_live",
                    action.to_dict(),
                    {
                        "bridge_result": {
                            "kind": action.kind,
                            "state": {
                                "state": "BetWait",
                                "mainButtonPushType": "Bet",
                            },
                        }
                    },
                )

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
        self.assertIsNone(runner.pending)
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

    def test_normal_completed_hands_excludes_bonus_results(self):
        state = reduce_event(
            BotGameState(phase="agari_sent"),
            event(
                1,
                "recv_janq_result",
                '{"status":"NORMAL","mjchip":1000,"nextMode":"ParenChallenge","tehai":[1,2,3]}',
            ),
        )
        state = reduce_event(
            state,
            event(
                2,
                "recv_game_haipai",
                '{"status":"PARENCHAN","zandan":3,"haipai":[1,2,3,4,5,6,7,8,9,10,11,12,13]}',
            ),
        )
        state = reduce_event(
            state,
            event(
                3,
                "recv_janq_result",
                '{"status":"PARENCHAN","mjchip":1200,"nextMode":"Normal","tehai":[1,2,3]}',
            ),
        )

        self.assertEqual(2, state.completed_hands)
        self.assertEqual(1, state.normal_completed_hands)

    def test_live_parenchan_result_counts_when_previous_mode_was_normal(self):
        state = reduce_event(
            BotGameState(phase="shoot_wait"),
            event(
                1,
                "game_state_snapshot",
                '{"gameMode":"Normal","state":"ShootWait","mainButtonType":"Shot","pais":[1,2,3]}',
            ),
        )
        state = reduce_event(
            state,
            event(
                2,
                "recv_janq_result",
                '{"status":"PARENCHAN","mjchip":840,"nextMode":"NONE","tehai":[1,2,3]}',
            ),
        )

        self.assertEqual(1, state.completed_hands)
        self.assertEqual(1, state.normal_completed_hands)
        self.assertEqual("ParenChallenge", state.mode)

    def test_live_parenchan_result_does_not_count_when_previous_mode_was_bonus(self):
        state = reduce_event(
            BotGameState(phase="shoot_wait"),
            event(
                1,
                "game_state_snapshot",
                (
                    '{"gameMode":"ParenChallenge","state":"ShootWait",'
                    '"mainButtonType":"Shot","pais":[1,2,3]}'
                ),
            ),
        )
        state = reduce_event(
            state,
            event(
                2,
                "recv_janq_result",
                '{"status":"PARENCHAN","mjchip":860,"nextMode":"NONE","tehai":[1,2,3]}',
            ),
        )

        self.assertEqual(1, state.completed_hands)
        self.assertEqual(0, state.normal_completed_hands)
        self.assertEqual("ParenChallenge", state.mode)

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

    def test_runner_max_normal_hands_waits_for_bonus_to_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            config = AutomationConfig(
                mode="dry_run",
                session_log_path=str(session_path),
                session_dir=tmp,
                max_hands=10,
                max_normal_hands=1,
                decision_cooldown_seconds=0,
            )
            runner = AutomationRunner(config, logger=SessionLogger(session_path))

            runner.process_event(
                event(
                    1,
                    "recv_janq_result",
                    '{"status":"NORMAL","mjchip":1000,"nextMode":"ParenChallenge","tehai":[1,2,3]}',
                ),
                now=1.0,
            )
            self.assertTrue(runner.running)
            runner.process_event(
                event(
                    2,
                    "recv_game_haipai",
                    '{"status":"PARENCHAN","zandan":3,"haipai":[1,2,3,4,5,6,7,8,9,10,11,12,13]}',
                ),
                now=2.0,
            )
            runner.process_event(
                event(
                    3,
                    "recv_janq_result",
                    '{"status":"PARENCHAN","mjchip":1200,"nextMode":"Normal","tehai":[1,2,3]}',
                ),
                now=3.0,
            )
            self.assertTrue(runner.running)
            runner.process_event(
                event(
                    4,
                    "game_state_snapshot",
                    '{"gameMode":"Normal","state":"BetWait","mainButtonType":"Bet","pais":[1,2,3]}',
                ),
                now=4.0,
            )
            rows = [
                json.loads(line)
                for line in session_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertFalse(runner.running)
        self.assertEqual("max_normal_hands", rows[-1]["payload"]["reason"])

    def test_runner_stops_at_bankruptcy_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            config = AutomationConfig(
                mode="dry_run",
                session_log_path=str(session_path),
                session_dir=tmp,
                bankruptcy_mjchip=9,
            )
            runner = AutomationRunner(config, logger=SessionLogger(session_path))
            runner.state = BotGameState(
                phase="bet_wait",
                mode="Normal",
                currency=CurrencyState(mjchip=9, start_mjchip=335),
            )

            should_stop = runner._should_stop(now=1.0, start=0.0)
            rows = [
                json.loads(line)
                for line in session_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertTrue(should_stop)
        self.assertEqual("bankruptcy_mjchip", rows[-1]["payload"]["reason"])
        self.assertEqual(9, rows[-1]["payload"]["mjchip"])

    def test_runner_waits_for_safe_point_before_bankruptcy_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            config = AutomationConfig(
                mode="dry_run",
                session_log_path=str(session_path),
                session_dir=tmp,
                bankruptcy_mjchip=9,
            )
            runner = AutomationRunner(config, logger=SessionLogger(session_path))
            runner.state = BotGameState(
                phase="shoot_wait",
                mode="Normal",
                currency=CurrencyState(mjchip=8, start_mjchip=568),
            )

            should_stop = runner._should_stop(now=1.0, start=0.0)
            rows = [
                json.loads(line)
                for line in session_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertFalse(should_stop)
        self.assertTrue(runner.running)
        self.assertEqual("bot_bankroll_terminal_wait", rows[-1]["type"])
        self.assertEqual("bankruptcy_mjchip", rows[-1]["payload"]["reason"])

    def test_runner_does_not_bet_after_bankruptcy_safe_point(self):
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
                bankruptcy_mjchip=9,
                decision_cooldown_seconds=0,
            )
            runner = AutomationRunner(config, logger=SessionLogger(session_path))
            fake = FakeExecutor()
            runner.executor = fake
            runner.state = BotGameState(
                currency=CurrencyState(mjchip=8, start_mjchip=568),
            )

            runner.process_event(
                event(
                    1,
                    "game_state_snapshot",
                    '{"gameMode":"Normal","state":"BetWait","mainButtonRequest":"Bet",'
                    '"betRate":10,"pais":[1,2,3]}',
                ),
                now=1.0,
            )
            rows = [
                json.loads(line)
                for line in session_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertFalse(runner.running)
        self.assertEqual([], fake.actions)
        self.assertEqual("bankruptcy_mjchip", rows[-1]["payload"]["reason"])

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

    def test_plugin_executor_retries_locked_result_file(self):
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

            original_read_text = Path.read_text
            attempts = {"locked": 0}

            def flaky_read_text(path, *args, **kwargs):
                if path.parent.name == "results" and attempts["locked"] == 0:
                    attempts["locked"] += 1
                    raise PermissionError("locked by writer")
                return original_read_text(path, *args, **kwargs)

            thread = threading.Thread(target=fake_plugin)
            thread.start()
            with mock.patch.object(Path, "read_text", flaky_read_text):
                result = executor.execute(BotAction("shot", area=4))
            thread.join(timeout=2)

        self.assertTrue(result.success)
        self.assertEqual(1, attempts["locked"])

    def test_plugin_executor_writes_login_account_command_without_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = AutomationConfig(
                mode="plugin_live",
                bridge_dir=tmp,
                action_delay_min_seconds=0,
                action_delay_max_seconds=0,
                bridge_result_timeout_seconds=2,
                login_timeout_seconds=2,
            )
            executor = PluginExecutor(config)
            commands = Path(tmp) / "commands"
            results = Path(tmp) / "results"
            captured = {}

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
                captured.update(command)
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
            result = executor.execute(
                BotAction(
                    "login_account",
                    account_request_id="req-a",
                    account_store_path=str(Path(tmp) / "accounts.json"),
                )
            )
            thread.join(timeout=2)

        self.assertTrue(result.success)
        self.assertEqual("login_account", captured["kind"])
        self.assertEqual("req-a", captured["accountRequestId"])
        self.assertNotIn("password", json.dumps(captured).lower())

    def test_account_batch_bridge_command_retries_locked_result_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = Path(tmp)
            commands = bridge / "commands"
            results = bridge / "results"

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

            original_read_text = Path.read_text
            attempts = {"locked": 0}

            def flaky_read_text(path, *args, **kwargs):
                if path.parent.name == "results" and attempts["locked"] == 0:
                    attempts["locked"] += 1
                    raise PermissionError("locked by writer")
                return original_read_text(path, *args, **kwargs)

            thread = threading.Thread(target=fake_plugin)
            thread.start()
            with mock.patch.object(Path, "read_text", flaky_read_text):
                result = send_bridge_command(
                    bridge,
                    "exit_to_login",
                    timeout_seconds=2,
                    poll_seconds=0.01,
                )
            thread.join(timeout=2)

        self.assertTrue(result["success"])
        self.assertEqual(1, attempts["locked"])

    def test_account_batch_summarizes_and_classifies_terminal_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            session_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "bot_state",
                                "payload": {
                                    "phase": "result",
                                    "completed_hands": 3,
                                    "currency": {"mjchip": 4000, "start_mjchip": 850},
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "bot_pause",
                                "payload": {"reason": "target_mjchip"},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "bot_session_summary",
                                "payload": {
                                    "state": {
                                        "phase": "result",
                                        "completed_hands": 3,
                                        "currency": {"mjchip": 4000, "start_mjchip": 850},
                                    }
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = summarize_session(session_path)
            status = classify_status(
                summary,
                returncode=0,
                target_mjchip=4000,
                bankruptcy_mjchip=9,
            )

        self.assertEqual(4000, summary["mjchip"])
        self.assertEqual(3, summary["completedHands"])
        self.assertTrue(status["terminal"])
        self.assertEqual("target_reached", status["accountStatus"])

    def test_account_batch_treats_rotation_quota_as_success(self):
        status = classify_status(
            {"mjchip": 900, "pauseReason": "max_normal_hands"},
            returncode=0,
            target_mjchip=4000,
            bankruptcy_mjchip=9,
        )

        self.assertTrue(status["terminal"])
        self.assertEqual("rotation_quota_reached", status["accountStatus"])

    def test_register_loop_marks_partial_bot_exception_as_resumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            accounts_path = root / "accounts.json"
            accounts_path.write_text(
                json.dumps(
                    [
                        {
                            "requestId": "req-a",
                            "loginId": "mja12345678",
                            "password": "secret-pass",
                            "nickname": "Mica02",
                            "finalMjchip": 850,
                            "status": "complete",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            session_path = root / "session.jsonl"
            session_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "bot_state",
                                "payload": {
                                    "phase": "shoot_wait",
                                    "completed_hands": 17,
                                    "currency": {"mjchip": 370, "start_mjchip": 850},
                                },
                            }
                        )
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            recovery = mark_account_after_loop_error(
                accounts_path,
                request_id="req-a",
                session_path=session_path,
                exc=RuntimeError(""),
                target_mjchip=100000,
                bankruptcy_mjchip=99,
            )
            rows = json.loads(accounts_path.read_text(encoding="utf-8"))

        self.assertTrue(recovery["updated"])
        self.assertTrue(rows[0]["status"].startswith("stopped_"))
        self.assertEqual(370, rows[0]["currentMjchip"])
        self.assertEqual(17, rows[0]["lastCompletedHands"])
        self.assertEqual("loop_exception:RuntimeError", rows[0]["lastTerminalReason"])

    def test_register_loop_attempts_exit_only_from_safe_phase(self):
        self.assertTrue(should_attempt_exit_to_login({"phase": "bet_wait"}))
        self.assertTrue(should_attempt_exit_to_login({"phase": "free_wait"}))
        self.assertFalse(should_attempt_exit_to_login({"phase": "result"}))
        self.assertFalse(should_attempt_exit_to_login({"phase": "shoot_wait"}))
        self.assertFalse(should_attempt_exit_to_login({}))

    def test_register_loop_resume_state_uses_matching_count_only(self):
        original_env_workspace = os.environ.get("JANQ_WORKSPACE")
        original_env_log = os.environ.get("JANQ_PROBE_LOG")
        original_root = register_loop_script.ROOT
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                register_loop_script.configure_root(root)
                register_loop_script.LOOP_STATUS.parent.mkdir(parents=True)
                register_loop_script.LOOP_STATUS.write_text(
                    json.dumps(
                        {
                            "state": "account_finished",
                            "count": 5,
                            "iteration": 3,
                            "failed": 1,
                            "attempt": 4,
                            "terminal": {"terminal": True},
                        }
                    ),
                    encoding="utf-8",
                )
                args = argparse.Namespace(count=5)

                resume = load_loop_resume_state(args)
                args.count = 6
                ignored = load_loop_resume_state(args)
                args.count = 5
                register_loop_script.LOOP_STATUS.write_text(
                    json.dumps(
                        {
                            "state": "account_prep_interrupted",
                            "count": 5,
                            "completed": 2,
                            "failed": 4,
                            "attempt": 9,
                        }
                    ),
                    encoding="utf-8",
                )
                interrupted_resume = load_loop_resume_state(args)

            self.assertEqual({"completed": 3, "failed": 1, "attempt": 4}, resume)
            self.assertEqual({}, ignored)
            self.assertEqual({"completed": 2, "failed": 4, "attempt": 9}, interrupted_resume)
        finally:
            register_loop_script.configure_root(original_root)
            if original_env_workspace is None:
                os.environ.pop("JANQ_WORKSPACE", None)
            else:
                os.environ["JANQ_WORKSPACE"] = original_env_workspace
            if original_env_log is None:
                os.environ.pop("JANQ_PROBE_LOG", None)
            else:
                os.environ["JANQ_PROBE_LOG"] = original_env_log

    def test_register_loop_preserves_active_prep_checkpoint_under_fresh_prep(self):
        request = {"id": "req-a", "nickname": "Mica01"}
        status = {
            "requestId": "req-a",
            "active": True,
            "accountCaptured": True,
            "stage": "finishing_first_resource_sync",
        }

        self.assertTrue(should_preserve_prep_state(request, status))

    def test_register_loop_preserves_failed_captured_prep_checkpoint_under_fresh_prep(self):
        request = {}
        status = {
            "requestId": "req-a",
            "active": False,
            "accountCaptured": True,
            "stage": "failed",
        }

        self.assertTrue(should_preserve_prep_state(request, status))

    def test_register_loop_allows_fresh_prep_when_checkpoint_is_complete(self):
        request = {"id": "req-a", "nickname": "Mica01"}
        status = {
            "requestId": "req-a",
            "active": False,
            "accountCaptured": True,
            "stage": "complete",
        }

        self.assertFalse(should_preserve_prep_state(request, status))

    def test_register_loop_does_not_resume_login_account_timeout(self):
        args = argparse.Namespace(bankruptcy_mjchip=99, target_mjchip=100000)
        row = {
            "requestId": "req-a",
            "status": "stopped_login_account_failed_local_action_timeout",
            "lastTerminalReason": "login_account_failed:local_action_timeout",
            "finalMjchip": 11870,
        }

        self.assertFalse(register_loop_script.is_resumable_account(row, args))

    def test_register_loop_still_resumes_runtime_nonterminal_stop(self):
        args = argparse.Namespace(bankruptcy_mjchip=99, target_mjchip=100000)
        row = {
            "requestId": "req-a",
            "status": "stopped_nonterminal_stop",
            "lastTerminalReason": "nonterminal_stop",
            "finalMjchip": 11870,
        }

        self.assertTrue(register_loop_script.is_resumable_account(row, args))

    def test_register_loop_interrupts_login_account_failure_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accounts.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "requestId": "req-a",
                            "loginId": "mja12345678",
                            "password": "secret-pass",
                            "nickname": "Mica01",
                            "finalMjchip": 11870,
                            "status": "registered",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            policy = register_loop_script.resolve_account_stop_policy(
                path,
                "req-a",
                {
                    "terminal": False,
                    "terminalReason": "login_account_failed:local_action_timeout",
                    "accountStatus": "stopped_login_account_failed_local_action_timeout",
                },
                max_resume_failures=5,
            )
            update_account_result(
                path,
                "req-a",
                current_mjchip=11870,
                status=policy["accountStatus"],
                terminal_reason=policy["terminalReason"],
                resume_failure_count=policy["resumeFailureCount"],
                resume_failure_limit=policy["resumeFailureLimit"],
                resume_failure_reason=policy["resumeFailureReason"],
                interrupted_at=policy["interruptedAt"],
            )
            rows = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(policy["interrupted"])
        self.assertEqual("interrupted_login_account_failed_local_action_timeout", rows[0]["status"])
        self.assertEqual(1, rows[0]["resumeFailureCount"])
        self.assertEqual(5, rows[0]["resumeFailureLimit"])
        self.assertIn("interruptedAt", rows[0])
        self.assertEqual("secret-pass", rows[0]["password"])

    def test_register_loop_allows_runtime_failure_before_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accounts.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "requestId": "req-a",
                            "loginId": "mja12345678",
                            "password": "secret-pass",
                            "status": "stopped_nonterminal_stop",
                            "lastTerminalReason": "nonterminal_stop",
                            "resumeFailureReason": "nonterminal_stop",
                            "resumeFailureCount": 3,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            policy = register_loop_script.resolve_account_stop_policy(
                path,
                "req-a",
                {
                    "terminal": False,
                    "terminalReason": "nonterminal_stop",
                    "accountStatus": "stopped_nonterminal_stop",
                },
                max_resume_failures=5,
            )

        self.assertFalse(policy["interrupted"])
        self.assertEqual("stopped_nonterminal_stop", policy["accountStatus"])
        self.assertEqual(4, policy["resumeFailureCount"])
        self.assertEqual(5, policy["resumeFailureLimit"])

    def test_register_loop_interrupts_runtime_failure_at_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accounts.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "requestId": "req-a",
                            "loginId": "mja12345678",
                            "password": "secret-pass",
                            "status": "stopped_nonterminal_stop",
                            "lastTerminalReason": "nonterminal_stop",
                            "resumeFailureReason": "nonterminal_stop",
                            "resumeFailureCount": 4,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            policy = register_loop_script.resolve_account_stop_policy(
                path,
                "req-a",
                {
                    "terminal": False,
                    "terminalReason": "nonterminal_stop",
                    "accountStatus": "stopped_nonterminal_stop",
                },
                max_resume_failures=5,
            )

        self.assertTrue(policy["interrupted"])
        self.assertEqual("interrupted_retry_exhausted_nonterminal_stop", policy["accountStatus"])
        self.assertEqual(5, policy["resumeFailureCount"])
        self.assertEqual(5, policy["resumeFailureLimit"])
        self.assertIsNotNone(policy["interruptedAt"])

    def test_register_loop_interrupts_prep_after_restart_limit(self):
        original_env_workspace = os.environ.get("JANQ_WORKSPACE")
        original_env_log = os.environ.get("JANQ_PROBE_LOG")
        original_root = register_loop_script.ROOT
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                register_loop_script.configure_root(root)
                register_loop_script.ACCOUNTS_PATH.parent.mkdir(parents=True)
                register_loop_script.ACCOUNTS_PATH.write_text(
                    json.dumps(
                        [
                            {
                                "requestId": "req-a",
                                "loginId": "mja12345678",
                                "password": "secret-pass",
                                "nickname": "Mica01",
                                "status": "registered",
                            }
                        ]
                    ),
                    encoding="utf-8",
                )
                register_loop_script.PREP_REQUEST.parent.mkdir(parents=True)
                register_loop_script.PREP_REQUEST.write_text(
                    json.dumps({"id": "req-a", "nickname": "Mica01"}),
                    encoding="utf-8",
                )
                args = argparse.Namespace(max_prep_restarts_per_account=5)

                payload = register_loop_script.interrupt_prep_account(
                    args,
                    request_id="req-a",
                    nickname="Mica01",
                    status={
                        "requestId": "req-a",
                        "nickname": "Mica01",
                        "active": True,
                        "accountCaptured": True,
                        "stage": "finishing_first_resource_sync",
                        "currentMjchip": 1200,
                    },
                    stage="finishing_first_resource_sync",
                    restarts=5,
                )
                rows = json.loads(register_loop_script.ACCOUNTS_PATH.read_text(encoding="utf-8"))
                interrupted_lines = register_loop_script.INTERRUPTED_ACCOUNTS_LOG.read_text(
                    encoding="utf-8"
                ).splitlines()

            self.assertTrue(payload["interrupted"])
            self.assertEqual("interrupted_prep_retry_exhausted_finishing_first_resource_sync", rows[0]["status"])
            self.assertEqual(5, rows[0]["resumeFailureCount"])
            self.assertEqual(5, rows[0]["resumeFailureLimit"])
            self.assertNotIn("currentMjchip", rows[0])
            self.assertNotIn("finalMjchip", rows[0])
            self.assertEqual(1, len(interrupted_lines))
            self.assertIn("prep_retry_exhausted:finishing_first_resource_sync", interrupted_lines[0])
        finally:
            register_loop_script.configure_root(original_root)
            if original_env_workspace is None:
                os.environ.pop("JANQ_WORKSPACE", None)
            else:
                os.environ["JANQ_WORKSPACE"] = original_env_workspace
            if original_env_log is None:
                os.environ.pop("JANQ_PROBE_LOG", None)
            else:
                os.environ["JANQ_PROBE_LOG"] = original_env_log

    def test_register_loop_writes_health_status(self):
        original_env_workspace = os.environ.get("JANQ_WORKSPACE")
        original_env_log = os.environ.get("JANQ_PROBE_LOG")
        original_root = register_loop_script.ROOT
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                register_loop_script.configure_root(root)

                with contextlib.redirect_stdout(io.StringIO()):
                    register_loop_script.write_loop_status(
                        {
                            "state": "account_prep_interrupted",
                            "count": 5,
                            "attempt": 3,
                            "requestId": "req-a",
                            "failed": 1,
                            "updatedAt": "2026-06-23T00:00:00+00:00",
                        }
                    )
                health = json.loads(register_loop_script.HEALTH_PATH.read_text(encoding="utf-8"))

            self.assertEqual("loop", health["source"])
            self.assertEqual("account_prep_interrupted", health["state"])
            self.assertEqual("req-a", health["requestId"])
            self.assertEqual(3, health["attempt"])
        finally:
            register_loop_script.configure_root(original_root)
            if original_env_workspace is None:
                os.environ.pop("JANQ_WORKSPACE", None)
            else:
                os.environ["JANQ_WORKSPACE"] = original_env_workspace
            if original_env_log is None:
                os.environ.pop("JANQ_PROBE_LOG", None)
            else:
                os.environ["JANQ_PROBE_LOG"] = original_env_log

    def test_register_loop_bridge_command_retries_locked_result_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = Path(tmp)
            commands = bridge / "commands"
            results = bridge / "results"

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

            original_read_text = Path.read_text
            attempts = {"locked": 0}

            def flaky_read_text(path, *args, **kwargs):
                if path.parent.name == "results" and attempts["locked"] == 0:
                    attempts["locked"] += 1
                    raise PermissionError("locked by writer")
                return original_read_text(path, *args, **kwargs)

            thread = threading.Thread(target=fake_plugin)
            thread.start()
            with mock.patch.object(Path, "read_text", flaky_read_text):
                result = send_register_bridge_command(
                    "exit_to_login",
                    bridge_dir=bridge,
                    timeout_seconds=2,
                    poll_seconds=0.01,
                )
            thread.join(timeout=2)

        self.assertTrue(result["success"])
        self.assertEqual(1, attempts["locked"])

    def test_register_loop_bridge_cleanup_removes_stale_queue_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = Path(tmp)
            commands = bridge / "commands"
            results = bridge / "results"
            commands.mkdir(parents=True)
            results.mkdir(parents=True)
            for directory in (commands, results):
                for name in ("a.json", "b.json.working", ".c.tmp"):
                    (directory / name).write_text("{}", encoding="utf-8")

            cleanup_register_bridge_files(bridge)

        self.assertEqual([], list(commands.glob("*")))
        self.assertEqual([], list(results.glob("*")))

    def test_register_loop_exception_text_keeps_empty_errors_actionable(self):
        self.assertEqual("TimeoutError", register_exception_text(TimeoutError()))

    def test_runner_logs_in_account_before_entering_janq(self):
        class FakeExecutor:
            def __init__(self):
                self.actions = []

            def execute(self, action, rng=None):
                del rng
                self.actions.append(action)
                return ExecutionResult(True, "plugin_live", action.to_dict(), {})

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            accounts_path = root / "accounts.json"
            accounts_path.write_text(
                json.dumps(
                    [
                        {
                            "requestId": "req-a",
                            "loginId": "mja12345678",
                            "password": "secret-pass",
                            "nickname": "JQreqa",
                            "finalMjchip": 850,
                            "status": "complete",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            session_path = root / "session.jsonl"
            config = AutomationConfig(
                mode="plugin_live",
                events_path=str(root / "events.jsonl"),
                bridge_dir=str(root / "bridge"),
                session_log_path=str(session_path),
                session_dir=str(root),
                login_account="req-a",
                account_store_path=str(accounts_path),
                enter_janq_on_start=True,
                max_runtime_seconds=0.01,
            )
            runner = AutomationRunner(config, logger=SessionLogger(session_path))
            fake = FakeExecutor()
            runner.executor = fake

            runner.run()

            rows = [
                json.loads(line)
                for line in session_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(["login_account", "enter_janq"], [action.kind for action in fake.actions])
        self.assertEqual("req-a", fake.actions[0].account_request_id)
        self.assertNotIn("secret-pass", json.dumps(rows))


if __name__ == "__main__":
    unittest.main()
