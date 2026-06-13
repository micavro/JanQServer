"""Fast baseline inspired by public JanQ攻略 heuristics."""

from __future__ import annotations

from janq_lab.assets.nyukyu import NyukyuTable
from janq_lab.model.hand import TileSet, is_complete_hand, tile_set, winning_tiles
from janq_lab.strategy.greedy import AreaDecision, DiscardDecision, choose_area_for_targets


MAN = frozenset(range(0, 9))
SOU = frozenset(range(9, 18))
PIN = frozenset(range(18, 27))
DRAGONS = frozenset((31, 32, 33))
HONORS = frozenset(range(27, 34))


def choose_public_area(hand: TileSet | list[int] | tuple[int, ...], table: NyukyuTable) -> AreaDecision:
    state = hand if isinstance(hand, TileSet) else tile_set(hand)
    winners = winning_tiles(state)
    if winners:
        return choose_area_for_targets(table, winners, "public_winning_tiles")

    target_group = _target_group(state)
    targets = tuple(tile_id for tile_id in sorted(target_group) if state.counts[tile_id] < 4)
    return choose_area_for_targets(table, targets, "public_route")


def choose_public_discard(hand: TileSet | list[int] | tuple[int, ...]) -> DiscardDecision:
    state = hand if isinstance(hand, TileSet) else tile_set(hand)
    if is_complete_hand(state):
        return DiscardDecision(True, None, None, (), "complete_hand")

    target_group = _target_group(state)
    candidates = [tile_id for tile_id, count in enumerate(state.counts) if count and tile_id not in target_group]
    if not candidates:
        candidates = [tile_id for tile_id, count in enumerate(state.counts) if count]

    discard = _least_protected_tile(state, candidates)
    return DiscardDecision(False, discard, None, (), "public_route_discard")


def _target_group(hand: TileSet) -> frozenset[int]:
    dragon_count = sum(hand.counts[tile_id] for tile_id in DRAGONS)
    if dragon_count >= 2:
        return DRAGONS

    groups = [
        (sum(hand.counts[tile_id] for tile_id in MAN), MAN),
        (sum(hand.counts[tile_id] for tile_id in PIN), PIN),
        (sum(hand.counts[tile_id] for tile_id in SOU), SOU),
    ]
    _, group = max(groups, key=lambda item: item[0])
    return group


def _least_protected_tile(hand: TileSet, candidates: list[int]) -> int:
    def score(tile_id: int) -> tuple[int, int, int]:
        count = hand.counts[tile_id]
        is_honor = 1 if tile_id in HONORS else 0
        is_edge = 1 if tile_id < 27 and tile_id % 9 in (0, 8) else 0
        return (count, -is_honor, -is_edge)

    return min(candidates, key=score)

