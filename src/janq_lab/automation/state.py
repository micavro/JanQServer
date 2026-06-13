"""Reduce JanqProbe events into the bot's current game state."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from janq_lab.model.hand import TileSet, tile_set
from janq_lab.probe.events import ProbeEvent
from janq_lab.probe.normalize import (
    api_tile_to_model,
    normalize_haipai_payload,
    normalize_result_payload,
    normalize_tsumo_payload,
)


BOT_EVENT_PREFIX = "bot_"
BLANK_MODEL_TILE = 9999


@dataclass(frozen=True)
class CurrencyState:
    gold: int | None = None
    mjchip: int | None = None
    cchip: int | None = None
    start_mjchip: int | None = None

    @property
    def delta_mjchip(self) -> int | None:
        if self.mjchip is None or self.start_mjchip is None:
            return None
        return self.mjchip - self.start_mjchip


@dataclass(frozen=True)
class BotGameState:
    phase: str = "unknown"
    mode: str | None = None
    status: str | None = None
    game_state: str | None = None
    main_button: str | None = None
    balls: int | None = None
    hand: tuple[int, ...] = ()
    dora: int | None = None
    ura_dora: int | None = None
    is_reach: bool = False
    hand_index: int = 0
    completed_hands: int = 0
    last_line: int = 0
    last_event_type: str | None = None
    currency: CurrencyState = CurrencyState()
    last_result: dict[str, Any] | None = None

    @property
    def hand_set(self) -> TileSet | None:
        if not self.hand or any(tile_id == BLANK_MODEL_TILE for tile_id in self.hand):
            return None
        try:
            return tile_set(self.hand)
        except ValueError:
            return None

    @property
    def decision_key(self) -> tuple[Any, ...]:
        return (
            self.phase,
            self.mode,
            self.status,
            self.balls,
            self.hand,
            self.is_reach,
            self.hand_index,
            self.completed_hands,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "mode": self.mode,
            "status": self.status,
            "game_state": self.game_state,
            "main_button": self.main_button,
            "balls": self.balls,
            "hand": self.hand,
            "dora": self.dora,
            "ura_dora": self.ura_dora,
            "is_reach": self.is_reach,
            "hand_index": self.hand_index,
            "completed_hands": self.completed_hands,
            "last_line": self.last_line,
            "last_event_type": self.last_event_type,
            "currency": self.currency.__dict__,
            "last_result": self.last_result,
        }


def reduce_event(state: BotGameState, event: ProbeEvent) -> BotGameState:
    if event.type.startswith(BOT_EVENT_PREFIX):
        return state

    payload = event.payload
    common = {
        "last_line": event.line_number,
        "last_event_type": event.type,
    }

    if event.type == "recv_game_haipai":
        normalized = normalize_haipai_payload(payload)
        hand = normalized["haipai"]
        phase = "user_wait" if len(hand) == 14 else "shoot_wait"
        currency = _currency_from_payload(payload, previous=state.currency)
        return replace(
            state,
            phase=phase,
            mode=_mode_from_status(payload.get("status")),
            status=_optional_str(payload.get("status")),
            game_state=None,
            main_button=None,
            balls=_optional_int(payload.get("zandan")),
            hand=hand,
            dora=normalized["dora"],
            ura_dora=normalized["ura_dora"],
            is_reach=False,
            hand_index=state.hand_index + 1,
            currency=currency,
            **common,
        )

    if event.type == "recv_game_tsumo":
        normalized = normalize_tsumo_payload(payload)
        currency = _currency_from_payload(payload, previous=state.currency)
        return replace(
            state,
            phase="user_wait",
            mode=_mode_from_status(payload.get("status")) or state.mode,
            status=_optional_str(payload.get("status")) or state.status,
            balls=_optional_int(payload.get("zandan")),
            hand=normalized["tehai"],
            dora=normalized["dora"] if normalized["dora"] is not None else state.dora,
            ura_dora=(
                normalized["ura_dora"] if normalized["ura_dora"] is not None else state.ura_dora
            ),
            is_reach=_optional_bool(payload.get("richi")) or False,
            currency=currency,
            **common,
        )

    if event.type == "recv_act_dahai":
        discard = api_tile_to_model(payload.get("sutehai"))
        next_hand = _remove_one(state.hand, discard)
        return replace(
            state,
            phase="shoot_wait" if len(next_hand) == 13 else "wait",
            hand=next_hand,
            is_reach=_optional_bool(payload.get("richi")) or state.is_reach,
            **common,
        )

    if event.type == "recv_janq_result":
        normalized = normalize_result_payload(payload)
        currency = _currency_from_payload(payload, previous=state.currency)
        return replace(
            state,
            phase="result",
            mode=_mode_from_status(payload.get("status")) or state.mode,
            status=_optional_str(payload.get("status")) or state.status,
            hand=normalized["tehai"] or state.hand,
            completed_hands=state.completed_hands + 1,
            currency=currency,
            last_result=dict(payload),
            **common,
        )

    if event.type == "game_state_snapshot":
        return _apply_snapshot(state, payload, **common)

    if event.type == "send_action_shot":
        return replace(state, phase="shot_sent", **common)
    if event.type == "send_action_dahai":
        return replace(state, phase="discard_sent", **common)
    if event.type == "send_action_agari":
        return replace(state, phase="agari_sent", **common)
    if event.type == "send_action_start":
        return replace(state, phase="start_sent", **common)

    return replace(state, **common)


def _apply_snapshot(state: BotGameState, payload: dict[str, Any], **common: Any) -> BotGameState:
    hand = _snapshot_hand(payload.get("pais"))
    game_state = _optional_str(payload.get("state")) or state.game_state
    main_button = _optional_str(payload.get("mainButtonType")) or state.main_button
    phase = _phase_from_snapshot(game_state, main_button, len(hand) or len(state.hand))
    return replace(
        state,
        phase=phase,
        mode=_optional_str(payload.get("gameMode")) or state.mode,
        game_state=game_state,
        main_button=main_button,
        balls=_optional_int(payload.get("balls")) if "balls" in payload else state.balls,
        hand=hand or state.hand,
        dora=_snapshot_tile(payload.get("dora")) if "dora" in payload else state.dora,
        ura_dora=_snapshot_tile(payload.get("uraDora")) if "uraDora" in payload else state.ura_dora,
        is_reach=_optional_bool(payload.get("isReach")) or state.is_reach,
        **common,
    )


def _phase_from_snapshot(game_state: str | None, main_button: str | None, hand_len: int) -> str:
    if main_button == "Bet":
        return "bet_wait"
    if main_button == "Free":
        return "free_wait"
    if main_button == "Agari":
        return "agari_wait"
    if game_state == "ShootWait" or main_button == "Shot":
        return "shoot_wait"
    if game_state == "UserWait":
        return "user_wait"
    if game_state == "Result":
        return "result"
    if hand_len == 14:
        return "user_wait"
    if hand_len == 13:
        return "shoot_wait"
    return "wait"


def _mode_from_status(value: Any) -> str | None:
    status = _optional_str(value)
    if status is None:
        return None
    return {
        "NORMAL": "Normal",
        "PARENCHAN": "ParenChallenge",
        "YAKUMAN": "YakumanBonus",
    }.get(status.upper(), status)


def _snapshot_hand(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    hand = []
    for item in value:
        if isinstance(item, int) and not isinstance(item, bool):
            if item == BLANK_MODEL_TILE:
                continue
            if 0 <= item <= 33:
                hand.append(item)
    return tuple(hand)


def _snapshot_tile(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 33:
        return value
    return None


def _remove_one(hand: tuple[int, ...], tile_id: int | None) -> tuple[int, ...]:
    if tile_id is None:
        return hand
    tiles = list(hand)
    try:
        tiles.remove(tile_id)
    except ValueError:
        return hand
    return tuple(tiles)


def _currency_from_payload(payload: dict[str, Any], *, previous: CurrencyState) -> CurrencyState:
    mjchip = _optional_int(payload.get("mjchip"))
    start_mjchip = previous.start_mjchip
    if start_mjchip is None and mjchip is not None:
        start_mjchip = mjchip
    return CurrencyState(
        gold=_optional_int(payload.get("gold")) if "gold" in payload else previous.gold,
        mjchip=mjchip if mjchip is not None else previous.mjchip,
        cchip=_optional_int(payload.get("cchip")) if "cchip" in payload else previous.cchip,
        start_mjchip=start_mjchip,
    )


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
