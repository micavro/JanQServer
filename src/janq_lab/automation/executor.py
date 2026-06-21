"""Action executors for dry-run and conservative UI live automation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import random
import time
from typing import Any
import uuid

from janq_lab.automation.config import AutomationConfig
from janq_lab.automation.policy import BotAction


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    mode: str
    action: dict[str, Any]
    details: dict[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DryRunExecutor:
    def __init__(self, config: AutomationConfig):
        self.config = config

    def execute(self, action: BotAction, rng: random.Random | None = None) -> ExecutionResult:
        del rng
        return ExecutionResult(
            success=True,
            mode="dry_run",
            action=action.to_dict(),
            details={"would_execute": True},
        )


class PluginExecutor:
    def __init__(self, config: AutomationConfig):
        self.config = config
        self.root = Path(config.bridge_dir)
        self.commands_dir = self.root / "commands"
        self.results_dir = self.root / "results"

    def execute(self, action: BotAction, rng: random.Random | None = None) -> ExecutionResult:
        source = rng if rng is not None else random.Random()
        delay = source.uniform(
            self.config.action_delay_min_seconds,
            self.config.action_delay_max_seconds,
        )
        time.sleep(delay)

        command_id = uuid.uuid4().hex
        command = {
            "id": command_id,
            "kind": action.kind,
            "createdAt": _utc_now(),
            "area": action.area,
            "discardIndex": action.discard_index,
            "discardTile": action.discard_tile,
            "richi": action.richi,
            "accountRequestId": action.account_request_id,
            "accountStorePath": action.account_store_path,
        }
        command_path = self.commands_dir / f"{command_id}.json"
        result_path = self.results_dir / f"{command_id}.json"
        temp_path = self.commands_dir / f".{command_id}.tmp"

        try:
            self.commands_dir.mkdir(parents=True, exist_ok=True)
            self.results_dir.mkdir(parents=True, exist_ok=True)
            result_path.unlink(missing_ok=True)
            temp_path.write_text(
                json.dumps(command, ensure_ascii=True, separators=(",", ":")),
                encoding="utf-8",
            )
            temp_path.replace(command_path)

            timeout = self.config.bridge_result_timeout_seconds
            if action.kind == "login_account":
                timeout = max(timeout, self.config.login_timeout_seconds)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if result_path.exists():
                    result = _read_json_when_ready(result_path, deadline=deadline)
                    if result is None:
                        time.sleep(0.05)
                        continue
                    _unlink_when_ready(result_path)
                    success = result.get("success") is True
                    return ExecutionResult(
                        success=success,
                        mode="plugin_live",
                        action=action.to_dict(),
                        details={
                            "command_id": command_id,
                            "delay_s": delay,
                            "timeout_s": timeout,
                            "bridge_result": result,
                        },
                        error=None if success else str(result.get("error") or "bridge_rejected"),
                    )
                time.sleep(0.05)
            return ExecutionResult(
                success=False,
                mode="plugin_live",
                action=action.to_dict(),
                details={"command_id": command_id, "delay_s": delay, "timeout_s": timeout},
                error="bridge_result_timeout",
            )
        except Exception as exc:
            return ExecutionResult(
                success=False,
                mode="plugin_live",
                action=action.to_dict(),
                details={"command_id": command_id, "delay_s": delay},
                error=str(exc),
            )
        finally:
            _unlink_when_ready(temp_path)


class UiExecutor:
    def __init__(self, config: AutomationConfig):
        self.config = config
        self._win32gui = None
        self._win32api = None
        self._win32con = None

    def execute(self, action: BotAction, rng: random.Random | None = None) -> ExecutionResult:
        source = rng if rng is not None else random.Random()
        try:
            self._ensure_win32()
            hwnd, rect = self._find_window()
            delay = source.uniform(
                self.config.action_delay_min_seconds,
                self.config.action_delay_max_seconds,
            )
            time.sleep(delay)
            if action.kind == "shot":
                if action.area is None:
                    raise ValueError("shot action requires area")
                x, y = self._primary_button_xy(rect, source)
                duration = self._shot_duration(action.area, source)
                self._hold_click(x, y, duration)
                details = {"hwnd": hwnd, "x": x, "y": y, "duration_ms": duration, "delay_s": delay}
            elif action.kind in ("press_main", "agari"):
                x, y = self._primary_button_xy(rect, source)
                duration = self._click_duration(source)
                self._hold_click(x, y, duration)
                details = {"hwnd": hwnd, "x": x, "y": y, "duration_ms": duration, "delay_s": delay}
            elif action.kind == "discard":
                if action.discard_index is None:
                    raise ValueError("discard action requires discard_index")
                if action.richi:
                    rx, ry = self._riichi_xy(rect, source)
                    self._hold_click(rx, ry, self._click_duration(source))
                    time.sleep(self.config.discard_click_interval_ms / 1000.0)
                x, y = self._tile_xy(rect, action.discard_index, source)
                duration = self._click_duration(source)
                for click_index in range(max(1, self.config.discard_clicks)):
                    if click_index:
                        time.sleep(self.config.discard_click_interval_ms / 1000.0)
                    self._hold_click(x, y, duration)
                details = {
                    "hwnd": hwnd,
                    "x": x,
                    "y": y,
                    "duration_ms": duration,
                    "delay_s": delay,
                    "clicks": max(1, self.config.discard_clicks),
                }
            else:
                raise ValueError(f"unknown action kind: {action.kind}")
            return ExecutionResult(True, "ui_live", action.to_dict(), details)
        except Exception as exc:
            return ExecutionResult(False, "ui_live", action.to_dict(), {}, error=str(exc))

    def _ensure_win32(self) -> None:
        if self._win32gui is not None:
            return
        try:
            import win32api  # type: ignore
            import win32con  # type: ignore
            import win32gui  # type: ignore
        except ImportError as exc:
            raise RuntimeError("ui_live requires pywin32") from exc
        self._win32api = win32api
        self._win32con = win32con
        self._win32gui = win32gui

    def _find_window(self) -> tuple[int, tuple[int, int, int, int]]:
        assert self._win32gui is not None
        matches: list[int] = []

        def callback(hwnd: int, _: Any) -> bool:
            if self._win32gui.IsWindowVisible(hwnd):
                title = self._win32gui.GetWindowText(hwnd)
                if self.config.window_title in title or "MJ" in title:
                    matches.append(hwnd)
            return True

        self._win32gui.EnumWindows(callback, None)
        if not matches:
            raise RuntimeError(f"game window not found: {self.config.window_title}")
        hwnd = matches[0]
        self._win32gui.ShowWindow(hwnd, self._win32con.SW_RESTORE)
        self._win32gui.SetForegroundWindow(hwnd)
        return hwnd, self._win32gui.GetWindowRect(hwnd)

    def _primary_button_xy(
        self,
        rect: tuple[int, int, int, int],
        rng: random.Random,
    ) -> tuple[int, int]:
        left, top, right, bottom = rect
        return self._jitter(
            int(left + (right - left) * self.config.primary_button_rel_x),
            int(top + (bottom - top) * self.config.primary_button_rel_y),
            rng,
        )

    def _tile_xy(
        self,
        rect: tuple[int, int, int, int],
        tile_index: int,
        rng: random.Random,
    ) -> tuple[int, int]:
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top
        if 1 <= tile_index <= 13:
            rel_x = self.config.hand_left_pct + (tile_index - 0.5) * (
                self.config.hand_width_pct / 13.0
            )
            rel_y = self.config.hand_top_pct + self.config.hand_height_pct / 2.0
        elif tile_index == 14:
            rel_x = self.config.drawn_left_pct + self.config.drawn_width_pct / 2.0
            rel_y = self.config.drawn_top_pct + self.config.drawn_height_pct / 2.0
        else:
            raise ValueError(f"discard_index must be 1..14, got {tile_index}")
        return self._jitter(
            int(left + width * rel_x / 100.0),
            int(top + height * rel_y / 100.0),
            rng,
        )

    def _riichi_xy(
        self,
        rect: tuple[int, int, int, int],
        rng: random.Random,
    ) -> tuple[int, int]:
        left, top, right, bottom = rect
        return self._jitter(
            int(left + (right - left) * self.config.riichi_rel_x),
            int(top + (bottom - top) * self.config.riichi_rel_y),
            rng,
        )

    def _shot_duration(self, area: int, rng: random.Random) -> int:
        base = self.config.shot_duration_ms(area)
        jitter = self.config.shot_duration_jitter_ms
        if jitter <= 0:
            return base
        return max(1, base + rng.randint(-jitter, jitter))

    def _click_duration(self, rng: random.Random) -> int:
        return rng.randint(
            self.config.click_duration_min_ms,
            self.config.click_duration_max_ms,
        )

    def _jitter(self, x: int, y: int, rng: random.Random) -> tuple[int, int]:
        amount = self.config.coord_jitter_px
        if amount <= 0:
            return x, y
        return x + rng.randint(-amount, amount), y + rng.randint(-amount, amount)

    def _hold_click(self, x: int, y: int, duration_ms: int) -> None:
        assert self._win32api is not None
        assert self._win32con is not None
        self._win32api.SetCursorPos((x, y))
        self._win32api.mouse_event(self._win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
        time.sleep(duration_ms / 1000.0)
        self._win32api.mouse_event(self._win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _read_json_when_ready(path: Path, *, deadline: float) -> dict[str, Any] | None:
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return None
        except (PermissionError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.05)
            continue
        if not isinstance(value, dict):
            raise ValueError(f"bridge result must be an object: {path}")
        return value
    if last_error is not None:
        raise last_error
    return None


def _unlink_when_ready(path: Path, *, attempts: int = 10) -> bool:
    for attempt in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return True
        except FileNotFoundError:
            return True
        except (PermissionError, OSError):
            if attempt + 1 >= attempts:
                return False
            time.sleep(0.05)
    return False


def make_executor(config: AutomationConfig) -> DryRunExecutor | PluginExecutor | UiExecutor:
    if config.mode == "dry_run":
        return DryRunExecutor(config)
    if config.mode == "plugin_live":
        return PluginExecutor(config)
    if config.mode == "ui_live":
        return UiExecutor(config)
    raise ValueError(f"unknown mode: {config.mode}")
