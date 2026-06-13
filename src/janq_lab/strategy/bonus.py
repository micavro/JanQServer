"""Bonus-mode policy for 3-ball tenpai games."""

from __future__ import annotations

from functools import lru_cache

from janq_lab.assets.nyukyu import AREA_COUNT, NyukyuTable, load_tables
from janq_lab.model.hand import TileSet, discard_options, is_complete_hand, tile_set, winning_tiles
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
) -> DiscardDecision:
    state = hand if isinstance(hand, TileSet) else tile_set(hand)
    if is_complete_hand(state):
        return DiscardDecision(True, None, None, (), "complete_hand")

    areas = _default_areas()
    scored = []
    for tile_id in discard_options(state):
        after = state.with_removed_one(tile_id)
        winners = winning_tiles(after)
        protected = tuple(i for i, count in enumerate(after.counts) if count == 3)
        best_score = max(
            _area_probability(after.counts, winners, area, areas) * 100.0
            + _area_probability(after.counts, protected, area, areas) * (14.0 + balls * 3.0)
            for area in range(1, AREA_COUNT + 1)
        )
        scored.append((best_score, -_keep_bias(state, tile_id), -tile_id, tile_id, winners))

    _, _, _, discard, winners = max(scored)
    return DiscardDecision(False, discard, None, winners, "bonus_wait_discard")


choose_bonus_area.uses_context = True  # type: ignore[attr-defined]
choose_bonus_discard.uses_context = True  # type: ignore[attr-defined]


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


def _keep_bias(hand: TileSet, tile_id: int) -> float:
    count = hand.counts[tile_id]
    if count >= 3:
        return 100.0
    if count == 2:
        return 8.0
    return 0.0


@lru_cache(maxsize=1)
def _default_areas() -> tuple[tuple[int, ...], ...]:
    return load_tables()["nyukyu_base_table.bytes"].areas
