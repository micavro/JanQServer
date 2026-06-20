"""Command-line JanQ automation runner."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import argparse
import random
import time
from typing import Iterator

from janq_lab.automation.accounts import select_account
from janq_lab.automation.bankroll import choose_bet_tier, parse_bet_ladder
from janq_lab.automation.config import AutomationConfig, load_config
from janq_lab.automation.executor import ExecutionResult, make_executor
from janq_lab.automation.policy import BotAction, BotDecision, StrategyPolicy
from janq_lab.automation.session_log import SessionLogger, default_session_log_path
from janq_lab.automation.state import BotGameState, reduce_event
from janq_lab.probe.events import ProbeEvent, parse_event


CONFIRMATION_EVENTS = {
    "shot": "send_action_shot",
    "discard": "send_action_dahai",
    "agari": "send_action_agari",
    "press_main": "send_action_start",
}

ACTIONABLE_PHASES = frozenset(("agari_wait", "bet_wait", "free_wait", "shoot_wait", "user_wait"))


@dataclass(frozen=True)
class PendingAction:
    action: BotAction
    expected_event: str
    started_at: float
    state_key: tuple[object, ...]


class ProbeTailer:
    def __init__(self, path: str | Path, *, start_at_end: bool = False):
        self.path = Path(path)
        self.offset = self.path.stat().st_size if start_at_end and self.path.exists() else 0
        self.line_number = 0

    def read_new_events(self) -> Iterator[ProbeEvent]:
        if not self.path.exists():
            return
        size = self.path.stat().st_size
        if size < self.offset:
            self.offset = 0
            self.line_number = 0
        with self.path.open("r", encoding="utf-8-sig") as handle:
            handle.seek(self.offset)
            for line in handle:
                self.line_number += 1
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    yield parse_event(stripped, line_number=self.line_number)
                except Exception:
                    continue
            self.offset = handle.tell()


class AutomationRunner:
    def __init__(
        self,
        config: AutomationConfig,
        *,
        logger: SessionLogger | None = None,
        rng: random.Random | None = None,
    ):
        self.config = config
        self.logger = logger or SessionLogger(
            config.session_log_path or default_session_log_path(config.session_dir)
        )
        self.rng = rng if rng is not None else random.Random()
        self.policy = StrategyPolicy(config.strategy)
        self.executor = make_executor(config)
        self.state = BotGameState()
        self.pending: PendingAction | None = None
        self.last_decision_key: tuple[object, ...] | None = None
        self.last_decision_time = 0.0
        self.start_completed_hands = 0
        self.start_normal_completed_hands = 0
        self.running = True
        self.bet_ladder = parse_bet_ladder(config.bet_ladder)
        initial_bet = config.forced_bet if config.forced_bet is not None else self.bet_ladder[0]
        self.target_bet = initial_bet
        self.selected_bet: int | None = None
        self.selected_bet_target: int | None = None
        self.selected_bet_mode: str | None = None
        self._last_bridge_target_bet: int | None = None
        self._bet_reselect_requested_at: float | None = None
        self._bet_reselect_target_bet: int | None = None

    def run(self) -> None:
        self.logger.write("bot_session_start", {"config": self.config.__dict__})
        self._update_bet_target("session_start")
        tailer = ProbeTailer(
            self.config.events_path,
            start_at_end=(
                self.config.mode != "dry_run"
                and not self.config.bootstrap_existing_events
            ),
        )
        if self.config.mode != "dry_run":
            for event in tailer.read_new_events():
                self._track_bet_selection(event)
                self.state = reduce_event(self.state, event)
            if self.config.bootstrap_existing_events and self.state.currency.mjchip is not None:
                self.state = replace(
                    self.state,
                    currency=replace(
                        self.state.currency,
                        start_mjchip=self.state.currency.mjchip,
                    ),
                )
            self.start_completed_hands = self.state.completed_hands
            self.start_normal_completed_hands = self.state.normal_completed_hands
            self.logger.write("bot_bootstrap_state", self.state.to_dict())
            self._update_bet_target("bootstrap_state")
        if self.config.mode == "plugin_live" and self.config.login_account:
            self._run_startup_account_login()
            self._drain_startup_events(tailer)
        if self.config.mode == "plugin_live" and self.running and self.config.enter_janq_on_start:
            startup_action = BotAction("enter_janq")
            startup_result = self.executor.execute(startup_action, self.rng)
            self.logger.write("bot_startup_action", startup_result.to_dict())
            if not startup_result.success:
                self._pause(f"startup_action_failed:{startup_result.error}")
            self._drain_startup_events(tailer)
        if self.config.mode != "dry_run":
            if self.running:
                self._maybe_decide(time.monotonic())
        start = time.monotonic()
        while self.running:
            now = time.monotonic()
            if self._should_stop(now, start):
                break
            any_event = False
            for event in tailer.read_new_events():
                any_event = True
                self.process_event(event, now=time.monotonic())
                if not self.running:
                    break
            self._check_pending_timeout(time.monotonic())
            self._check_bet_reselect_timeout(time.monotonic())
            if not any_event:
                time.sleep(self.config.poll_interval_seconds)
        self.logger.write(
            "bot_session_summary",
            {
                "state": self.state.to_dict(),
                "pending": None if self.pending is None else self.pending.action.to_dict(),
            },
        )

    def process_event(self, event: ProbeEvent, *, now: float | None = None) -> None:
        current_time = now if now is not None else time.monotonic()
        old_state = self.state
        self.state = reduce_event(self.state, event)
        if self.state != old_state:
            self.logger.write("bot_state", self.state.to_dict())
            self._update_bet_target("state_change")
        self._track_bet_selection(event)
        if event.type in ("janq_navigation_login_blocked", "janq_runtime_login_blocked"):
            self._pause(
                "login_blocked_or_repeated_dialog",
                payload={"probe_payload": event.payload},
            )
            return
        if event.type == "janq_navigation_reselect_failed":
            self._pause(
                "bet_reselect_failed_to_leave_game",
                payload={"probe_payload": event.payload},
            )
            return
        if self.state.phase not in ACTIONABLE_PHASES:
            self.last_decision_key = None
        if self.pending is not None and event.type == self.pending.expected_event:
            mismatch = _confirmation_payload_mismatch(self.pending.action, event)
            if mismatch is not None:
                self._pause(
                    f"confirmation_payload_mismatch:{self.pending.expected_event}",
                    payload={
                        "action": self.pending.action.to_dict(),
                        "probe_payload": event.payload,
                        "mismatch": mismatch,
                    },
                )
                return
            self.logger.write(
                "bot_confirmed",
                {
                    "expected_event": self.pending.expected_event,
                    "action": self.pending.action.to_dict(),
                    "probe_line": event.line_number,
                },
            )
            self.pending = None
        if self._completed_this_session() >= self.config.max_hands:
            self.logger.write("bot_pause", {"reason": "max_hands"})
            self.running = False
            return
        if self._pause_if_normal_hand_quota_reached():
            return
        self._maybe_decide(current_time)

    def _run_startup_account_login(self) -> None:
        try:
            account_path = Path(self.config.account_store_path)
            if not account_path.is_absolute():
                account_path = Path.cwd() / account_path
            account = select_account(account_path, self.config.login_account or "")
        except Exception as exc:
            self._pause("account_selection_failed", payload={"error": str(exc)})
            return

        self.logger.write("bot_login_account_selected", account.public_payload())
        action = BotAction(
            "login_account",
            account_request_id=account.request_id,
            account_store_path=str(account_path),
        )
        result = self.executor.execute(action, self.rng)
        self.logger.write("bot_login_account_action", result.to_dict())
        if not result.success:
            self._pause(f"login_account_failed:{result.error}")

    def _drain_startup_events(self, tailer: ProbeTailer) -> None:
        if not self.running:
            return
        changed = False
        for event in tailer.read_new_events():
            self._track_bet_selection(event)
            old_state = self.state
            self.state = reduce_event(self.state, event)
            changed = changed or self.state != old_state
        if changed:
            self.logger.write("bot_startup_state", self.state.to_dict())
            self._update_bet_target("startup_events")

    def _track_bet_selection(self, event: ProbeEvent) -> None:
        if event.type in ("probe_loaded", "probe_unloaded"):
            self.selected_bet = None
            self.selected_bet_target = None
            self.selected_bet_mode = None
            self._bet_reselect_requested_at = None
            self._bet_reselect_target_bet = None
            self.logger.write("bot_bet_selection_reset", {"event_type": event.type})
            return
        if event.type == "janq_navigation_ready":
            self._track_current_game_bet(event, source="navigation_ready")
            return
        if event.type == "game_state_snapshot":
            self._track_current_game_bet(event, source="game_state_snapshot")
            return
        if event.type == "janq_navigation_bet_selected":
            payload = event.payload or {}
            bet = _optional_int(payload.get("bet"))
            if bet is not None:
                self.selected_bet = bet
                self.selected_bet_target = _optional_int(payload.get("targetBet"))
                mode = payload.get("selectionMode")
                self.selected_bet_mode = mode if isinstance(mode, str) else None
                self._bet_reselect_requested_at = None
                self._bet_reselect_target_bet = None
                self.logger.write(
                    "bot_bet_selected",
                    {
                        "bet": bet,
                        "target_bet": self.target_bet,
                        "selection_target_bet": self.selected_bet_target,
                        "selection_mode": self.selected_bet_mode,
                        "payload": event.payload,
                    },
                )

    def _track_current_game_bet(self, event: ProbeEvent, *, source: str) -> None:
        payload = event.payload or {}
        bet = _optional_int(payload.get("currentBet"))
        if bet is None:
            bet = _optional_int(payload.get("betRate"))
        if bet is None or bet <= 0:
            return
        if self.selected_bet == bet and self.selected_bet_mode == source:
            return
        self.selected_bet = bet
        self.selected_bet_target = None
        self.selected_bet_mode = source
        self.logger.write(
            "bot_current_bet_observed",
            {
                "bet": bet,
                "target_bet": self.target_bet,
                "source": source,
                "payload": payload,
            },
        )

    def _maybe_decide(self, now: float) -> None:
        if self.pending is not None:
            return
        if self.state.phase == "bet_wait" and self._bet_selection_status() != "ready":
            self._handle_bet_selection_gap(now)
            return
        if self.state.decision_key == self.last_decision_key:
            return
        if now - self.last_decision_time < self.config.decision_cooldown_seconds:
            return
        decision = self.policy.decide(self.state)
        if decision.action is None:
            return
        self.logger.write("bot_decision", decision.to_dict())
        result = self.executor.execute(decision.action, self.rng)
        self.logger.write("bot_action_done", result.to_dict())
        self.last_decision_key = decision.state_key
        self.last_decision_time = now
        if not result.success:
            self._pause(f"action_failed:{result.error}")
            return
        if self.config.mode != "dry_run":
            expected_event = CONFIRMATION_EVENTS.get(decision.action.kind)
            if expected_event is not None and not _bridge_result_confirms_action(decision.action, result):
                self.pending = PendingAction(
                    action=decision.action,
                    expected_event=expected_event,
                    started_at=now,
                    state_key=decision.state_key,
                )

    def _check_pending_timeout(self, now: float) -> None:
        if self.pending is None:
            return
        if now - self.pending.started_at > self.config.confirm_timeout_seconds:
            self._pause(
                f"confirmation_timeout:{self.pending.expected_event}",
                payload={"action": self.pending.action.to_dict()},
            )

    def _check_bet_reselect_timeout(self, now: float) -> None:
        if self._bet_reselect_requested_at is None:
            return
        if now - self._bet_reselect_requested_at <= self.config.bet_reselect_timeout_seconds:
            return
        self._pause(
            "bet_reselect_timeout",
            payload={
                "selected_bet": self.selected_bet,
                "selection_target_bet": self.selected_bet_target,
                "selection_mode": self.selected_bet_mode,
                "target_bet": self.target_bet,
                "requested_target_bet": self._bet_reselect_target_bet,
                "mjchip": self.state.currency.mjchip,
            },
        )

    def _should_stop(self, now: float, start: float) -> bool:
        if self._completed_this_session() >= self.config.max_hands:
            self.logger.write("bot_pause", {"reason": "max_hands"})
            return True
        if self._pause_if_normal_hand_quota_reached():
            return True
        if now - start >= self.config.max_runtime_seconds:
            self.logger.write("bot_pause", {"reason": "max_runtime_seconds"})
            return True
        delta = self.state.currency.delta_mjchip
        if delta is not None:
            if self.config.stop_loss_mjchip is not None and delta <= -abs(self.config.stop_loss_mjchip):
                self.logger.write("bot_pause", {"reason": "stop_loss_mjchip", "delta_mjchip": delta})
                return True
            if self.config.stop_win_mjchip is not None and delta >= abs(self.config.stop_win_mjchip):
                self.logger.write("bot_pause", {"reason": "stop_win_mjchip", "delta_mjchip": delta})
                return True
        if (
            self.config.target_mjchip is not None
            and self.state.currency.mjchip is not None
            and self.state.currency.mjchip >= self.config.target_mjchip
        ):
            self.logger.write(
                "bot_pause",
                {
                    "reason": "target_mjchip",
                    "mjchip": self.state.currency.mjchip,
                    "target_mjchip": self.config.target_mjchip,
                },
            )
            return True
        if (
            self.config.bankruptcy_mjchip is not None
            and self.state.currency.mjchip is not None
            and self.state.currency.mjchip <= self.config.bankruptcy_mjchip
        ):
            self.logger.write(
                "bot_pause",
                {
                    "reason": "bankruptcy_mjchip",
                    "mjchip": self.state.currency.mjchip,
                    "bankruptcy_mjchip": self.config.bankruptcy_mjchip,
                },
            )
            return True
        return False

    def _completed_this_session(self) -> int:
        return self.state.completed_hands - self.start_completed_hands

    def _normal_completed_this_session(self) -> int:
        return self.state.normal_completed_hands - self.start_normal_completed_hands

    def _pause_if_normal_hand_quota_reached(self) -> bool:
        if self.config.max_normal_hands is None:
            return False
        normal_hands = self._normal_completed_this_session()
        if normal_hands < self.config.max_normal_hands:
            return False
        if not self._at_normal_safe_stop_point():
            self.logger.write(
                "bot_normal_hand_quota_wait",
                {
                    "normal_hands": normal_hands,
                    "max_normal_hands": self.config.max_normal_hands,
                    "state": self.state.to_dict(),
                },
            )
            return False
        self.logger.write(
            "bot_pause",
            {
                "reason": "max_normal_hands",
                "normal_hands": normal_hands,
                "max_normal_hands": self.config.max_normal_hands,
                "state": self.state.to_dict(),
            },
        )
        self.running = False
        return True

    def _at_normal_safe_stop_point(self) -> bool:
        if self.state.mode in ("ParenChallenge", "YakumanBonus"):
            return False
        if self.state.mode is None and self.state.status in ("PARENCHAN", "YAKUMAN"):
            return False
        next_mode = None
        if isinstance(self.state.last_result, dict):
            next_mode = self.state.last_result.get("nextMode")
        if next_mode in ("ParenChallenge", "YakumanBonus", "PARENCHAN", "YAKUMAN"):
            return False
        return self.state.phase in ("bet_wait", "free_wait")

    def _pause(self, reason: str, payload: dict[str, object] | None = None) -> None:
        body: dict[str, object] = {"reason": reason, "state": self.state.to_dict()}
        if payload:
            body.update(payload)
        self.logger.write("bot_pause", body)
        self.running = False

    def _update_bet_target(self, reason: str) -> None:
        decision = choose_bet_tier(
            self.state.currency.mjchip,
            self.bet_ladder,
            current_bet=self.target_bet,
            forced_bet=self.config.forced_bet,
            up_multiple=self.config.bet_up_multiple,
            down_multiple=self.config.bet_down_multiple,
        )
        changed = decision.bet != self.target_bet
        self.target_bet = decision.bet
        if changed or reason == "session_start" or self._last_bridge_target_bet != self.target_bet:
            self._write_bridge_settings(decision.reason)
            self.logger.write(
                "bot_bet_policy",
                {
                    "reason": reason,
                    "target_bet": self.target_bet,
                    "selected_bet": self.selected_bet,
                    "policy_reason": decision.reason,
                    "mjchip": self.state.currency.mjchip,
                    "bet_ladder": self.bet_ladder,
                },
            )

    def _write_bridge_settings(self, policy_reason: str) -> None:
        path = Path(self.config.bridge_dir) / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "targetBet": self.target_bet,
            "betLadder": list(self.bet_ladder),
            "betPolicy": "bankroll_200_100",
            "betUpMultiple": self.config.bet_up_multiple,
            "betDownMultiple": self.config.bet_down_multiple,
            "targetMjchip": self.config.target_mjchip,
            "bankruptcyMjchip": self.config.bankruptcy_mjchip,
            "currentMjchip": self.state.currency.mjchip,
            "policyReason": policy_reason,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        temp_path = path.with_name(f".{path.name}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )
        for attempt in range(10):
            try:
                temp_path.replace(path)
                self._last_bridge_target_bet = self.target_bet
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)

    def _bet_tier_change_required(self) -> bool:
        return self._bet_selection_status() != "ready"

    def _bet_selection_status(self) -> str:
        if self.config.mode != "plugin_live":
            if self.selected_bet is None:
                return "ready"
            return "ready" if self.selected_bet == self.target_bet else "mismatch"
        if self.selected_bet is None:
            return "unknown" if self.config.require_selected_bet_confirmation else "ready"
        if self.selected_bet != self.target_bet:
            if self.selected_bet_target == self.target_bet:
                return "fallback_mismatch"
            return "stale"
        if self.selected_bet_target is not None and self.selected_bet_target != self.target_bet:
            return "stale"
        return "ready"

    def _handle_bet_selection_gap(self, now: float) -> None:
        status = self._bet_selection_status()
        payload = {
            "status": status,
            "selected_bet": self.selected_bet,
            "selection_target_bet": self.selected_bet_target,
            "selection_mode": self.selected_bet_mode,
            "target_bet": self.target_bet,
            "mjchip": self.state.currency.mjchip,
        }
        if status == "fallback_mismatch":
            self._pause("bet_target_unavailable_or_fallback", payload=payload)
            return
        if self.config.mode != "plugin_live" or not self.config.auto_reselect_bet:
            self._pause("bet_tier_change_required", payload=payload)
            return
        if self._bet_reselect_requested_at is not None:
            self.logger.write("bot_bet_reselect_wait", payload)
            return

        action = BotAction("reselect_bet")
        self.logger.write("bot_bet_reselect_requested", {"action": action.to_dict(), **payload})
        result = self.executor.execute(action, self.rng)
        self.logger.write("bot_bet_reselect_action", result.to_dict())
        if not result.success:
            self._pause(f"bet_reselect_failed:{result.error}", payload=payload)
            return
        self._bet_reselect_requested_at = now
        self._bet_reselect_target_bet = self.target_bet
        self.last_decision_time = now


def decide_once(config: AutomationConfig, events: list[ProbeEvent]) -> BotDecision:
    runner = AutomationRunner(config, logger=SessionLogger(Path(config.session_dir) / "decide_once.jsonl"))
    for event in events:
        runner.state = reduce_event(runner.state, event)
    return runner.policy.decide(runner.state)


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _confirmation_payload_mismatch(action: BotAction, event: ProbeEvent) -> str | None:
    if action.kind == "shot" and event.type == "send_action_shot":
        actual = _optional_int(event.payload.get("area"))
        if action.area is not None and actual != action.area:
            return f"shot_area:{action.area}!={actual}"
    return None


def _bridge_result_confirms_action(action: BotAction, result: ExecutionResult) -> bool:
    if action.kind not in ("shot", "agari"):
        return False
    bridge_result = result.details.get("bridge_result") if isinstance(result.details, dict) else None
    if not isinstance(bridge_result, dict) or bridge_result.get("kind") != action.kind:
        return False
    state = bridge_result.get("state")
    if not isinstance(state, dict):
        return False
    if action.kind == "shot":
        return state.get("state") in ("UserWait", "BetWait", "FreeWait", "Result", "AgariRun")
    return state.get("state") in ("Result", "AgariRun", "BetWait")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run JanQ automation in dry-run or UI-live mode.")
    parser.add_argument("--config", default=None, help="automation.yaml or JSON config path")
    parser.add_argument("--mode", choices=("dry_run", "plugin_live", "ui_live"), default=None)
    parser.add_argument("--events-path", default=None)
    parser.add_argument("--bridge-dir", default=None)
    parser.add_argument("--session-log-path", default=None)
    parser.add_argument("--bootstrap-existing-events", action="store_true", default=None)
    parser.add_argument("--login-account", default=None)
    parser.add_argument("--account-store-path", default=None)
    parser.add_argument("--login-timeout-seconds", type=float, default=None)
    parser.add_argument("--strategy", choices=("public", "greedy", "route_ev", "route_ev2"), default=None)
    parser.add_argument("--max-hands", type=int, default=None)
    parser.add_argument("--max-normal-hands", type=int, default=None)
    parser.add_argument("--max-runtime-seconds", type=float, default=None)
    parser.add_argument("--target-mjchip", type=int, default=None)
    parser.add_argument("--bankruptcy-mjchip", type=int, default=None)
    parser.add_argument("--forced-bet", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args(argv)

    config = load_config(
        args.config,
        mode=args.mode,
        events_path=args.events_path,
        bridge_dir=args.bridge_dir,
        session_log_path=args.session_log_path,
        bootstrap_existing_events=args.bootstrap_existing_events,
        login_account=args.login_account,
        account_store_path=args.account_store_path,
        login_timeout_seconds=args.login_timeout_seconds,
        strategy=args.strategy,
        max_hands=args.max_hands,
        max_normal_hands=args.max_normal_hands,
        max_runtime_seconds=args.max_runtime_seconds,
        target_mjchip=args.target_mjchip,
        bankruptcy_mjchip=args.bankruptcy_mjchip,
        forced_bet=args.forced_bet,
    )
    runner = AutomationRunner(config, rng=random.Random(args.seed))
    runner.run()


if __name__ == "__main__":
    main()
