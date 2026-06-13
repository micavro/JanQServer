"""Decision adapter from bot state to executable actions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from janq_lab.assets.nyukyu import load_tables
from janq_lab.automation.state import BotGameState
from janq_lab.model.hand import is_complete_hand, tile_set
from janq_lab.strategy.bonus import choose_bonus_area, choose_bonus_discard
from janq_lab.strategy.greedy import (
    AreaDecision,
    DiscardDecision,
    choose_greedy_area,
    choose_greedy_discard,
)
from janq_lab.strategy.public import choose_public_area, choose_public_discard
from janq_lab.strategy.route_ev import choose_route_ev_area, choose_route_ev_discard


@dataclass(frozen=True)
class BotAction:
    kind: str
    area: int | None = None
    discard_index: int | None = None
    discard_tile: int | None = None
    richi: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BotDecision:
    action: BotAction | None
    reason: str
    strategy: str
    state_key: tuple[Any, ...]
    area_decision: dict[str, Any] | None = None
    discard_decision: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": None if self.action is None else self.action.to_dict(),
            "reason": self.reason,
            "strategy": self.strategy,
            "state_key": self.state_key,
            "area_decision": self.area_decision,
            "discard_decision": self.discard_decision,
        }


class StrategyPolicy:
    def __init__(self, strategy: str = "route_ev"):
        self.strategy = strategy
        self.tables = load_tables()
        self.normal_table = self.tables["nyukyu_base_table.bytes"]
        self.paren_table = self.tables["nyukyu_paren_table.bytes"]
        self.yakuman_table = self.tables["nyukyu_yakuman_table.bytes"]

    def decide(self, state: BotGameState) -> BotDecision:
        if state.phase in ("bet_wait", "free_wait"):
            return BotDecision(
                action=BotAction("press_main"),
                reason=state.phase,
                strategy=self.strategy,
                state_key=state.decision_key,
            )
        if state.phase == "agari_wait":
            return BotDecision(
                action=BotAction("agari"),
                reason="main_button_agari",
                strategy=self.strategy,
                state_key=state.decision_key,
            )
        if state.phase == "shoot_wait":
            return self._decide_shot(state)
        if state.phase == "user_wait":
            return self._decide_user_wait(state)
        return BotDecision(
            action=None,
            reason=f"not_actionable_phase:{state.phase}",
            strategy=self.strategy,
            state_key=state.decision_key,
        )

    def _decide_shot(self, state: BotGameState) -> BotDecision:
        if len(state.hand) != 13:
            return BotDecision(None, f"shoot_wait_requires_13_tiles:{len(state.hand)}", self.strategy, state.decision_key)
        hand = tile_set(state.hand)
        balls = state.balls if state.balls is not None else 1
        area_decision = self._choose_area(state, hand, balls)
        return BotDecision(
            action=BotAction("shot", area=area_decision.area),
            reason=area_decision.reason,
            strategy=self.strategy,
            state_key=state.decision_key,
            area_decision=_area_to_dict(area_decision),
        )

    def _decide_user_wait(self, state: BotGameState) -> BotDecision:
        if len(state.hand) != 14:
            return BotDecision(None, f"user_wait_requires_14_tiles:{len(state.hand)}", self.strategy, state.decision_key)
        hand = tile_set(state.hand)
        if is_complete_hand(hand):
            return BotDecision(
                action=BotAction("agari"),
                reason="complete_hand",
                strategy=self.strategy,
                state_key=state.decision_key,
            )

        balls = state.balls if state.balls is not None else 1
        discard_decision = self._choose_discard(state, hand, balls)
        if discard_decision.is_agari:
            return BotDecision(
                action=BotAction("agari"),
                reason=discard_decision.reason,
                strategy=self.strategy,
                state_key=state.decision_key,
                discard_decision=_discard_to_dict(discard_decision),
            )
        if discard_decision.discard_tile is None:
            return BotDecision(
                action=None,
                reason="discard_policy_returned_no_tile",
                strategy=self.strategy,
                state_key=state.decision_key,
                discard_decision=_discard_to_dict(discard_decision),
            )

        discard_index, reason = _discard_index_for_tile(
            state.hand,
            discard_decision.discard_tile,
            force_drawn=state.is_reach,
        )
        return BotDecision(
            action=BotAction(
                "discard",
                discard_index=discard_index,
                discard_tile=state.hand[discard_index - 1],
                richi=False,
            ),
            reason=f"{discard_decision.reason}:{reason}",
            strategy=self.strategy,
            state_key=state.decision_key,
            discard_decision=_discard_to_dict(discard_decision),
        )

    def _choose_area(self, state: BotGameState, hand: Any, balls: int) -> AreaDecision:
        if _is_bonus_mode(state):
            table = self.yakuman_table if state.mode == "YakumanBonus" else self.paren_table
            return choose_bonus_area(hand, table, balls)
        if self.strategy == "public":
            return choose_public_area(hand, self.normal_table)
        if self.strategy == "greedy":
            return choose_greedy_area(hand, self.normal_table)
        if self.strategy == "route_ev":
            return choose_route_ev_area(hand, self.normal_table, balls)
        raise ValueError(f"unknown strategy: {self.strategy}")

    def _choose_discard(self, state: BotGameState, hand: Any, balls: int) -> DiscardDecision:
        if _is_bonus_mode(state):
            return choose_bonus_discard(hand, balls)
        if self.strategy == "public":
            return choose_public_discard(hand)
        if self.strategy == "greedy":
            return choose_greedy_discard(hand)
        if self.strategy == "route_ev":
            return choose_route_ev_discard(hand, balls)
        raise ValueError(f"unknown strategy: {self.strategy}")


def _is_bonus_mode(state: BotGameState) -> bool:
    mode = (state.mode or "").lower()
    status = (state.status or "").lower()
    return mode in ("parenchallenge", "yakumanbonus") or status in ("parenchan", "yakuman")


def _discard_index_for_tile(
    hand: tuple[int, ...],
    tile_id: int,
    *,
    force_drawn: bool,
) -> tuple[int, str]:
    if force_drawn and len(hand) >= 14:
        return 14, "reach_forces_drawn_tile"
    matches = [index for index, current in enumerate(hand, start=1) if current == tile_id]
    if not matches:
        raise ValueError(f"discard tile {tile_id} is not in hand {hand}")
    return matches[-1], "rightmost_matching_tile"


def _area_to_dict(decision: AreaDecision) -> dict[str, Any]:
    return {
        "area": decision.area,
        "target_tiles": decision.target_tiles,
        "target_weight": decision.target_weight,
        "probability": decision.probability,
        "reason": decision.reason,
    }


def _discard_to_dict(decision: DiscardDecision) -> dict[str, Any]:
    return {
        "is_agari": decision.is_agari,
        "discard_tile": decision.discard_tile,
        "shanten_after": decision.shanten_after,
        "accepts": decision.accepts,
        "reason": decision.reason,
    }
