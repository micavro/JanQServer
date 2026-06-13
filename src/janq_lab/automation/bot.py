"""Command-line JanQ automation runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import argparse
import random
import time
from typing import Iterator

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


@dataclass(frozen=True)
class PendingAction:
    action: BotAction
    expected_event: str
    started_at: float
    state_key: tuple[object, ...]


class ProbeTailer:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.offset = 0
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
        self.running = True

    def run(self) -> None:
        self.logger.write("bot_session_start", {"config": self.config.__dict__})
        tailer = ProbeTailer(self.config.events_path)
        start = time.monotonic()
        while self.running:
            now = time.monotonic()
            if self._should_stop(now, start):
                break
            any_event = False
            for event in tailer.read_new_events():
                any_event = True
                self.process_event(event, now=time.monotonic())
            self._check_pending_timeout(time.monotonic())
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
        if self.pending is not None and event.type == self.pending.expected_event:
            self.logger.write(
                "bot_confirmed",
                {
                    "expected_event": self.pending.expected_event,
                    "action": self.pending.action.to_dict(),
                    "probe_line": event.line_number,
                },
            )
            self.pending = None
        self._maybe_decide(current_time)

    def _maybe_decide(self, now: float) -> None:
        if self.pending is not None:
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
            if expected_event is not None:
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

    def _should_stop(self, now: float, start: float) -> bool:
        if self.state.completed_hands >= self.config.max_hands:
            self.logger.write("bot_pause", {"reason": "max_hands"})
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
        return False

    def _pause(self, reason: str, payload: dict[str, object] | None = None) -> None:
        body: dict[str, object] = {"reason": reason, "state": self.state.to_dict()}
        if payload:
            body.update(payload)
        self.logger.write("bot_pause", body)
        self.running = False


def decide_once(config: AutomationConfig, events: list[ProbeEvent]) -> BotDecision:
    runner = AutomationRunner(config, logger=SessionLogger(Path(config.session_dir) / "decide_once.jsonl"))
    for event in events:
        runner.state = reduce_event(runner.state, event)
    return runner.policy.decide(runner.state)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run JanQ automation in dry-run or UI-live mode.")
    parser.add_argument("--config", default=None, help="automation.yaml or JSON config path")
    parser.add_argument("--mode", choices=("dry_run", "ui_live"), default=None)
    parser.add_argument("--events-path", default=None)
    parser.add_argument("--session-log-path", default=None)
    parser.add_argument("--strategy", choices=("public", "greedy", "route_ev"), default=None)
    parser.add_argument("--max-hands", type=int, default=None)
    parser.add_argument("--max-runtime-seconds", type=float, default=None)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args(argv)

    config = load_config(
        args.config,
        mode=args.mode,
        events_path=args.events_path,
        session_log_path=args.session_log_path,
        strategy=args.strategy,
        max_hands=args.max_hands,
        max_runtime_seconds=args.max_runtime_seconds,
    )
    runner = AutomationRunner(config, rng=random.Random(args.seed))
    runner.run()


if __name__ == "__main__":
    main()
