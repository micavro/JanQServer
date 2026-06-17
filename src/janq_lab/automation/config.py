"""Configuration for the JanQ automation runner."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
import json
from pathlib import Path
from typing import Any

from janq_lab.automation.bankroll import parse_bet_ladder


@dataclass(frozen=True)
class AutomationConfig:
    mode: str = "dry_run"
    events_path: str = "_runtime/logs/janq_events.jsonl"
    bridge_dir: str = "_runtime/bridge"
    bridge_result_timeout_seconds: float = 18.0
    enter_janq_on_start: bool = False
    bootstrap_existing_events: bool = False
    session_log_path: str | None = None
    session_dir: str = "_runtime/sessions"
    strategy: str = "route_ev"
    max_hands: int = 100
    max_runtime_seconds: float = 3600.0
    stop_loss_mjchip: int | None = None
    stop_win_mjchip: int | None = None
    target_mjchip: int | None = None
    forced_bet: int | None = None
    bet_ladder: str = "10,20,30,50,100,200"
    bet_up_multiple: float = 200.0
    bet_down_multiple: float = 100.0
    auto_reselect_bet: bool = True
    require_selected_bet_confirmation: bool = True
    bet_reselect_timeout_seconds: float = 45.0
    poll_interval_seconds: float = 0.2
    decision_cooldown_seconds: float = 0.8
    confirm_timeout_seconds: float = 12.0
    action_delay_min_seconds: float = 0.45
    action_delay_max_seconds: float = 1.35
    window_title: str = "セガNET麻雀 MJ"
    primary_button_rel_x: float = 0.93
    primary_button_rel_y: float = 0.90
    hand_left_pct: float = 10.0
    hand_top_pct: float = 82.4
    hand_width_pct: float = 64.5
    hand_height_pct: float = 11.8
    drawn_left_pct: float = 74.7
    drawn_top_pct: float = 82.4
    drawn_width_pct: float = 4.8
    drawn_height_pct: float = 11.8
    riichi_rel_x: float = 0.937
    riichi_rel_y: float = 0.578
    click_duration_min_ms: int = 70
    click_duration_max_ms: int = 145
    discard_clicks: int = 2
    discard_click_interval_ms: int = 90
    coord_jitter_px: int = 2
    shot_duration_jitter_ms: int = 17
    shot_area_1_ms: int = 300
    shot_area_2_ms: int = 400
    shot_area_3_ms: int = 600
    shot_area_4_ms: int = 685
    shot_area_5_ms: int = 800
    shot_area_6_ms: int = 1000
    shot_area_7_ms: int = 1300
    dry_run_log_actions: bool = True

    def validate(self) -> None:
        if self.mode not in ("dry_run", "plugin_live", "ui_live"):
            raise ValueError("mode must be dry_run, plugin_live, or ui_live")
        if self.strategy not in ("public", "greedy", "route_ev"):
            raise ValueError("strategy must be public, greedy, or route_ev")
        if self.max_hands < 1:
            raise ValueError("max_hands must be positive")
        if self.max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be positive")
        if self.target_mjchip is not None and self.target_mjchip <= 0:
            raise ValueError("target_mjchip must be positive")
        if self.forced_bet is not None and self.forced_bet <= 0:
            raise ValueError("forced_bet must be positive")
        if self.bet_up_multiple <= 0 or self.bet_down_multiple <= 0:
            raise ValueError("bet multiples must be positive")
        if self.bet_reselect_timeout_seconds <= 0:
            raise ValueError("bet_reselect_timeout_seconds must be positive")
        parse_bet_ladder(self.bet_ladder)
        if self.action_delay_min_seconds < 0 or self.action_delay_max_seconds < 0:
            raise ValueError("action delays must be non-negative")
        if self.action_delay_min_seconds > self.action_delay_max_seconds:
            raise ValueError("action_delay_min_seconds cannot exceed action_delay_max_seconds")
        if self.confirm_timeout_seconds <= 0:
            raise ValueError("confirm_timeout_seconds must be positive")
        if self.bridge_result_timeout_seconds <= 0:
            raise ValueError("bridge_result_timeout_seconds must be positive")
        for area in range(1, 8):
            if self.shot_duration_ms(area) <= 0:
                raise ValueError(f"shot area {area} duration must be positive")

    def shot_duration_ms(self, area: int) -> int:
        if not 1 <= area <= 7:
            raise ValueError(f"area must be 1..7, got {area}")
        return int(getattr(self, f"shot_area_{area}_ms"))


def load_config(path: str | Path | None = None, **overrides: Any) -> AutomationConfig:
    data: dict[str, Any] = {}
    if path is not None:
        config_path = Path(path)
        if config_path.exists():
            text = config_path.read_text(encoding="utf-8-sig")
            if config_path.suffix.lower() == ".json":
                data.update(json.loads(text))
            else:
                data.update(_parse_simple_yaml(text))
        else:
            raise FileNotFoundError(config_path)
    data.update({key: value for key, value in overrides.items() if value is not None})
    valid = {field.name for field in fields(AutomationConfig)}
    unknown = sorted(set(data) - valid)
    if unknown:
        raise ValueError(f"unknown automation config keys: {', '.join(unknown)}")
    config = replace(AutomationConfig(), **data)
    config.validate()
    return config


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            raise ValueError(f"invalid config line: {raw_line!r}")
        key, value = line.split(":", 1)
        values[key.strip()] = _parse_scalar(value.strip())
    return values


def _parse_scalar(value: str) -> Any:
    if value == "" or value.lower() in ("null", "none"):
        return None
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
