"""Offline JanQ simulator skeleton."""

from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Callable

from janq_lab.assets.nyukyu import NyukyuTable
from janq_lab.model.haipai import random_wall_hand
from janq_lab.model.hand import (
    TileSet,
    is_complete_hand,
    shanten,
    tile_set,
    winning_tiles,
)
from janq_lab.strategy.greedy import (
    AreaDecision,
    DiscardDecision,
    choose_greedy_area,
    choose_greedy_discard,
)


ChooseArea = Callable[..., AreaDecision]
ChooseDiscard = Callable[..., DiscardDecision]


@dataclass(frozen=True)
class ShotEvent:
    area: int
    tile_id: int
    balls_before: int
    balls_after: int
    fourth_copy: bool
    replays: int
    area_reason: str


@dataclass(frozen=True)
class TurnEvent:
    shot: ShotEvent
    discard: DiscardDecision
    riichi_before: bool = False
    riichi_declared: bool = False
    ippatsu_chance: bool = False


@dataclass(frozen=True)
class SimulationResult:
    win: bool
    turns: tuple[TurnEvent, ...]
    final_hand: TileSet
    riichi: bool = False
    riichi_turn: int | None = None
    double_riichi: bool = False
    ippatsu_win: bool = False

    @property
    def shots(self) -> int:
        return len(self.turns)


def simulate_hand(
    initial_hand: list[int] | tuple[int, ...] | TileSet,
    table: NyukyuTable,
    *,
    balls: int = 8,
    rng: random.Random | None = None,
    choose_area: ChooseArea = choose_greedy_area,
    choose_discard: ChooseDiscard = choose_greedy_discard,
    max_replays: int = 100,
    max_turns: int = 100,
    dora_id: int | None = None,
    ura_dora_id: int | None = None,
    hold_hand: bool = False,
) -> SimulationResult:
    """Simulate one normal JanQ hand.

    The first version models a server draw as resampling impossible fifth copies.
    A draw that creates the fourth copy of a tile refunds one ball.
    """

    source = rng if rng is not None else random.Random()
    hand = initial_hand if isinstance(initial_hand, TileSet) else tile_set(initial_hand)
    if hand.size != 13:
        raise ValueError(f"initial_hand must have 13 tiles, got {hand.size}")

    turns: list[TurnEvent] = []
    riichi_active = False
    riichi_turn: int | None = None
    shots_after_riichi = 0
    while balls > 0 and len(turns) < max_turns:
        area_decision = _call_choose_area(
            choose_area,
            hand,
            table,
            balls,
            dora_id=dora_id,
            ura_dora_id=ura_dora_id,
            is_reach=riichi_active,
        )
        balls_before = balls
        balls -= 1
        ippatsu_chance = riichi_active and shots_after_riichi == 0

        replays = 0
        while True:
            tile_id = table.draw(area_decision.area, source)
            if hand.can_add(tile_id):
                break
            replays += 1
            if replays > max_replays:
                raise RuntimeError("too many impossible draw replays")

        fourth_copy = hand.counts[tile_id] == 3
        hand = hand.with_added(tile_id)
        if fourth_copy:
            balls += 1

        if hold_hand:
            if is_complete_hand(hand):
                discard_decision = DiscardDecision(
                    True,
                    None,
                    None,
                    (),
                    "bonus_hold_agari",
                )
            else:
                locked_hand = hand.with_removed_one(tile_id)
                discard_decision = DiscardDecision(
                    False,
                    tile_id,
                    shanten(locked_hand),
                    winning_tiles(locked_hand),
                    "bonus_hold_auto_discard",
                )
        else:
            discard_decision = _call_choose_discard(
                choose_discard,
                hand,
                balls,
                dora_id=dora_id,
                ura_dora_id=ura_dora_id,
                is_reach=riichi_active,
                turn=len(turns) + 1,
                drawn_tile=tile_id,
            )
        shot = ShotEvent(
            area=area_decision.area,
            tile_id=tile_id,
            balls_before=balls_before,
            balls_after=balls,
            fourth_copy=fourth_copy,
            replays=replays,
            area_reason=area_decision.reason,
        )
        riichi_declared = (not riichi_active) and discard_decision.declare_riichi
        turns.append(
            TurnEvent(
                shot=shot,
                discard=discard_decision,
                riichi_before=riichi_active,
                riichi_declared=riichi_declared,
                ippatsu_chance=ippatsu_chance,
            )
        )

        if discard_decision.is_agari:
            return SimulationResult(
                win=True,
                turns=tuple(turns),
                final_hand=hand,
                riichi=riichi_active,
                riichi_turn=riichi_turn,
                double_riichi=riichi_turn == 1,
                ippatsu_win=ippatsu_chance,
            )

        if discard_decision.discard_tile is None:
            raise RuntimeError("non-agari discard decision did not include a tile")
        hand = hand.with_removed_one(discard_decision.discard_tile)
        if riichi_declared:
            riichi_active = True
            riichi_turn = len(turns)
            shots_after_riichi = 0
        elif riichi_active:
            shots_after_riichi += 1

    return SimulationResult(
        win=False,
        turns=tuple(turns),
        final_hand=hand,
        riichi=riichi_active,
        riichi_turn=riichi_turn,
        double_riichi=riichi_turn == 1,
        ippatsu_win=False,
    )


def random_initial_hand(rng: random.Random | None = None) -> TileSet:
    return random_wall_hand(rng)


def _call_choose_area(
    choose_area: ChooseArea,
    hand: TileSet,
    table: NyukyuTable,
    balls: int,
    *,
    dora_id: int | None = None,
    ura_dora_id: int | None = None,
    is_reach: bool = False,
) -> AreaDecision:
    if getattr(choose_area, "uses_full_context", False):
        return choose_area(
            hand,
            table,
            balls,
            dora_id=dora_id,
            ura_dora_id=ura_dora_id,
            is_reach=is_reach,
        )
    if getattr(choose_area, "uses_context", False):
        return choose_area(hand, table, balls)
    return choose_area(hand, table)


def _call_choose_discard(
    choose_discard: ChooseDiscard,
    hand: TileSet,
    balls: int,
    *,
    dora_id: int | None = None,
    ura_dora_id: int | None = None,
    is_reach: bool = False,
    turn: int | None = None,
    drawn_tile: int | None = None,
) -> DiscardDecision:
    if getattr(choose_discard, "uses_full_context", False):
        return choose_discard(
            hand,
            balls,
            dora_id=dora_id,
            ura_dora_id=ura_dora_id,
            is_reach=is_reach,
            turn=turn,
            drawn_tile=drawn_tile,
        )
    if getattr(choose_discard, "uses_context", False):
        return choose_discard(hand, balls)
    return choose_discard(hand)
