"""Bonus-mode policy for 3-ball HOLD-locked tenpai games."""

from __future__ import annotations

from janq_lab.assets.nyukyu import AREA_COUNT, NyukyuTable
from janq_lab.model.hand import TileSet, is_complete_hand, shanten, tile_set, winning_tiles
from janq_lab.strategy.greedy import AreaDecision, DiscardDecision


def choose_bonus_area(
    hand: TileSet | list[int] | tuple[int, ...],
    table: NyukyuTable,
    balls: int,
) -> AreaDecision:
    state = hand if isinstance(hand, TileSet) else tile_set(hand)
    winners = winning_tiles(state)
    protected = tuple(tile_id for tile_id, count in enumerate(state.counts) if count == 3)

    scored = []
    for area in range(1, AREA_COUNT + 1):
        win_p = _area_probability(state.counts, winners, area, table.areas)
        protect_p = _area_probability(state.counts, protected, area, table.areas)
        score = win_p * 100.0 + protect_p * (14.0 + balls * 3.0)
        scored.append((score, win_p, protect_p, area))

    score, win_p, protect_p, area = max(scored)
    return AreaDecision(
        area=area,
        target_tiles=winners,
        target_weight=sum(table.tile_weight(area, tile_id) for tile_id in winners),
        reason=f"bonus_wait:win={win_p:.3f}:protect={protect_p:.3f}:v={score:.2f}",
    )


def choose_bonus_discard(
    hand: TileSet | list[int] | tuple[int, ...],
    balls: int,
    *,
    drawn_tile: int | None = None,
    **_: object,
) -> DiscardDecision:
    del balls
    state = hand if isinstance(hand, TileSet) else tile_set(hand)
    if is_complete_hand(state):
        return DiscardDecision(True, None, None, (), "bonus_hold_agari")
    if drawn_tile is None or state.counts[drawn_tile] <= 0:
        raise ValueError("bonus HOLD discard requires the drawn tile")
    locked_hand = state.with_removed_one(drawn_tile)
    return DiscardDecision(
        False,
        drawn_tile,
        shanten(locked_hand),
        winning_tiles(locked_hand),
        "bonus_hold_auto_discard",
    )


choose_bonus_area.uses_context = True  # type: ignore[attr-defined]
choose_bonus_discard.uses_full_context = True  # type: ignore[attr-defined]


def _area_probability(
    counts: tuple[int, ...],
    targets: tuple[int, ...],
    area: int,
    areas: tuple[tuple[int, ...], ...],
) -> float:
    if not targets:
        return 0.0
    target_set = set(targets)
    weights = areas[area - 1]
    valid_total = sum(weight for tile_id, weight in enumerate(weights) if counts[tile_id] < 4)
    if valid_total <= 0:
        return 0.0
    hit = sum(
        weight
        for tile_id, weight in enumerate(weights)
        if tile_id in target_set and counts[tile_id] < 4
    )
    return hit / valid_total
