"""Small greedy baseline policies.

These policies are intentionally simple. They are useful as a regression
baseline and as scaffolding for later EV-aware search.
"""

from __future__ import annotations

from dataclasses import dataclass

from janq_lab.assets.nyukyu import AREA_COUNT, NyukyuTable
from janq_lab.model.hand import (
    TileSet,
    discard_options,
    improving_tiles,
    is_complete_hand,
    shanten,
    tile_set,
    winning_tiles,
)


@dataclass(frozen=True)
class AreaDecision:
    area: int
    target_tiles: tuple[int, ...]
    target_weight: int
    reason: str

    @property
    def probability(self) -> float:
        return self.target_weight / 10000


@dataclass(frozen=True)
class DiscardDecision:
    is_agari: bool
    discard_tile: int | None
    shanten_after: int | None
    accepts: tuple[int, ...]
    reason: str
    declare_riichi: bool = False


def choose_area_for_targets(table: NyukyuTable, targets: tuple[int, ...], reason: str) -> AreaDecision:
    if not targets:
        return AreaDecision(area=4, target_tiles=(), target_weight=0, reason="fallback")

    scores = [
        (sum(table.tile_weight(area, tile_id) for tile_id in targets), area)
        for area in range(1, AREA_COUNT + 1)
    ]
    target_weight, area = max(scores)
    return AreaDecision(
        area=area,
        target_tiles=targets,
        target_weight=target_weight,
        reason=reason,
    )


def choose_greedy_area(hand: TileSet | list[int] | tuple[int, ...], table: NyukyuTable) -> AreaDecision:
    state = hand if isinstance(hand, TileSet) else tile_set(hand)
    winners = winning_tiles(state)
    if winners:
        return choose_area_for_targets(table, winners, "winning_tiles")

    better = improving_tiles(state)
    if better:
        return choose_area_for_targets(table, better, "improving_tiles")

    return AreaDecision(area=4, target_tiles=(), target_weight=0, reason="fallback")


def choose_greedy_discard(hand: TileSet | list[int] | tuple[int, ...]) -> DiscardDecision:
    state = hand if isinstance(hand, TileSet) else tile_set(hand)
    if is_complete_hand(state):
        return DiscardDecision(
            is_agari=True,
            discard_tile=None,
            shanten_after=None,
            accepts=(),
            reason="complete_hand",
        )

    scored: list[tuple[int, int, int, tuple[int, ...]]] = []
    for tile_id in discard_options(state):
        after = state.with_removed_one(tile_id)
        after_shanten = shanten(after)
        accepts = improving_tiles(after)
        scored.append((after_shanten, -len(accepts), tile_id, accepts))

    if not scored:
        raise ValueError("cannot choose discard from an empty hand")

    after_shanten, accepts_score, tile_id, accepts = min(scored)
    return DiscardDecision(
        is_agari=False,
        discard_tile=tile_id,
        shanten_after=after_shanten,
        accepts=accepts,
        reason=f"min_shanten_accepts_{-accepts_score}",
    )
