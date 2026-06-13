"""Basic closed-hand mahjong utilities for JanQ simulation."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

from janq_lab.tiles import TILE_COUNT, check_tile_id, tile_name


TERMINAL_AND_HONOR_IDS = frozenset(
    (0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33)
)


@dataclass(frozen=True)
class TileSet:
    counts: tuple[int, ...]

    @classmethod
    def from_tiles(cls, tiles: Iterable[int]) -> "TileSet":
        counts = [0] * TILE_COUNT
        for tile_id in tiles:
            check_tile_id(tile_id)
            counts[tile_id] += 1
            if counts[tile_id] > 4:
                raise ValueError(f"tile {tile_name(tile_id)} appears more than 4 times")
        return cls(tuple(counts))

    @property
    def size(self) -> int:
        return sum(self.counts)

    def to_tiles(self) -> tuple[int, ...]:
        return tuple(
            tile_id
            for tile_id, count in enumerate(self.counts)
            for _ in range(count)
        )

    def with_added(self, tile_id: int) -> "TileSet":
        check_tile_id(tile_id)
        counts = list(self.counts)
        counts[tile_id] += 1
        if counts[tile_id] > 4:
            raise ValueError(f"tile {tile_name(tile_id)} appears more than 4 times")
        return TileSet(tuple(counts))

    def with_removed_one(self, tile_id: int) -> "TileSet":
        check_tile_id(tile_id)
        counts = list(self.counts)
        if counts[tile_id] <= 0:
            raise ValueError(f"tile {tile_name(tile_id)} is not in hand")
        counts[tile_id] -= 1
        return TileSet(tuple(counts))

    def can_add(self, tile_id: int) -> bool:
        check_tile_id(tile_id)
        return self.counts[tile_id] < 4


def tile_set(tiles: Iterable[int]) -> TileSet:
    return TileSet.from_tiles(tiles)


def shanten(tiles: Iterable[int] | TileSet) -> int:
    hand = tiles if isinstance(tiles, TileSet) else tile_set(tiles)
    return _shanten_counts_cached(hand.counts)


def is_complete_hand(tiles: Iterable[int] | TileSet) -> bool:
    hand = tiles if isinstance(tiles, TileSet) else tile_set(tiles)
    if hand.size % 3 != 2:
        return False
    return _is_complete_counts_cached(hand.counts)


def winning_tiles(tiles: Iterable[int] | TileSet) -> tuple[int, ...]:
    hand = tiles if isinstance(tiles, TileSet) else tile_set(tiles)
    if hand.size % 3 != 1:
        return ()
    return _winning_tiles_cached(hand.counts)


def improving_tiles(tiles: Iterable[int] | TileSet) -> tuple[int, ...]:
    hand = tiles if isinstance(tiles, TileSet) else tile_set(tiles)
    return _improving_tiles_cached(hand.counts)


def discard_options(tiles: Iterable[int] | TileSet) -> tuple[int, ...]:
    hand = tiles if isinstance(tiles, TileSet) else tile_set(tiles)
    return tuple(tile_id for tile_id, count in enumerate(hand.counts) if count)


def best_discards_by_shanten(tiles: Iterable[int] | TileSet) -> tuple[int, ...]:
    hand = tiles if isinstance(tiles, TileSet) else tile_set(tiles)
    if hand.size % 3 != 2:
        return ()
    return _best_discards_by_shanten_cached(hand.counts)


@lru_cache(maxsize=500_000)
def _shanten_counts_cached(counts: tuple[int, ...]) -> int:
    return min(
        standard_shanten(counts),
        chiitoitsu_shanten(counts),
        kokushi_shanten(counts),
    )


@lru_cache(maxsize=500_000)
def _is_complete_counts_cached(counts: tuple[int, ...]) -> bool:
    if sum(counts) % 3 != 2:
        return False
    if _is_chiitoitsu_complete(counts) or _is_kokushi_complete(counts):
        return True

    for tile_id, count in enumerate(counts):
        if count < 2:
            continue
        work = list(counts)
        work[tile_id] -= 2
        if _can_form_all_melds_cached(tuple(work)):
            return True
    return False


@lru_cache(maxsize=500_000)
def _can_form_all_melds_cached(counts: tuple[int, ...]) -> bool:
    try:
        tile_id = next(i for i, count in enumerate(counts) if count)
    except StopIteration:
        return True

    if counts[tile_id] >= 3:
        work = list(counts)
        work[tile_id] -= 3
        if _can_form_all_melds_cached(tuple(work)):
            return True

    if _can_sequence(tile_id) and counts[tile_id + 1] and counts[tile_id + 2]:
        work = list(counts)
        work[tile_id] -= 1
        work[tile_id + 1] -= 1
        work[tile_id + 2] -= 1
        if _can_form_all_melds_cached(tuple(work)):
            return True

    return False


def _is_chiitoitsu_complete(counts: tuple[int, ...]) -> bool:
    return sum(1 for count in counts if count == 2) == 7


def _is_kokushi_complete(counts: tuple[int, ...]) -> bool:
    return all(counts[tile_id] for tile_id in TERMINAL_AND_HONOR_IDS) and any(
        counts[tile_id] >= 2 for tile_id in TERMINAL_AND_HONOR_IDS
    )


@lru_cache(maxsize=500_000)
def _winning_tiles_cached(counts: tuple[int, ...]) -> tuple[int, ...]:
    winners = []
    hand = TileSet(counts)
    for tile_id in range(TILE_COUNT):
        if hand.can_add(tile_id) and is_complete_hand(hand.with_added(tile_id)):
            winners.append(tile_id)
    return tuple(winners)


@lru_cache(maxsize=500_000)
def _improving_tiles_cached(counts: tuple[int, ...]) -> tuple[int, ...]:
    hand = TileSet(counts)
    current = shanten(hand)
    better = []
    for tile_id in range(TILE_COUNT):
        if hand.can_add(tile_id) and shanten(hand.with_added(tile_id)) < current:
            better.append(tile_id)
    return tuple(better)


@lru_cache(maxsize=500_000)
def _best_discards_by_shanten_cached(counts: tuple[int, ...]) -> tuple[int, ...]:
    hand = TileSet(counts)

    scored: list[tuple[int, int, int]] = []
    for tile_id in discard_options(hand):
        after = hand.with_removed_one(tile_id)
        after_shanten = shanten(after)
        accepts = len(improving_tiles(after))
        scored.append((after_shanten, -accepts, tile_id))

    if not scored:
        return ()

    best_shanten, best_accepts, _ = min(scored)
    return tuple(
        tile_id
        for after_shanten, accepts, tile_id in scored
        if after_shanten == best_shanten and accepts == best_accepts
    )


def standard_shanten(counts: tuple[int, ...]) -> int:
    _validate_counts(counts)
    return _standard_shanten_cached(tuple(counts))


@lru_cache(maxsize=200_000)
def _standard_shanten_cached(counts: tuple[int, ...]) -> int:
    work = list(counts)
    best = 8

    def finish(melds: int, taatsu: int, pair: int) -> None:
        nonlocal best
        capped_taatsu = min(taatsu, 4 - melds)
        value = 8 - melds * 2 - capped_taatsu - pair
        if value < best:
            best = value

    def dfs(index: int, melds: int, taatsu: int, pair: int) -> None:
        nonlocal best
        while index < TILE_COUNT and work[index] == 0:
            index += 1
        if index >= TILE_COUNT:
            finish(melds, taatsu, pair)
            return

        if melds > 4 or taatsu > 4:
            finish(melds, taatsu, pair)
            return

        if work[index] >= 3:
            work[index] -= 3
            dfs(index, melds + 1, taatsu, pair)
            work[index] += 3

        if _can_sequence(index) and work[index + 1] and work[index + 2]:
            work[index] -= 1
            work[index + 1] -= 1
            work[index + 2] -= 1
            dfs(index, melds + 1, taatsu, pair)
            work[index] += 1
            work[index + 1] += 1
            work[index + 2] += 1

        if work[index] >= 2:
            work[index] -= 2
            if pair == 0:
                dfs(index, melds, taatsu, 1)
            dfs(index, melds, taatsu + 1, pair)
            work[index] += 2

        if _can_adjacent_wait(index) and work[index + 1]:
            work[index] -= 1
            work[index + 1] -= 1
            dfs(index, melds, taatsu + 1, pair)
            work[index] += 1
            work[index + 1] += 1

        if _can_closed_wait(index) and work[index + 2]:
            work[index] -= 1
            work[index + 2] -= 1
            dfs(index, melds, taatsu + 1, pair)
            work[index] += 1
            work[index + 2] += 1

        work[index] -= 1
        dfs(index, melds, taatsu, pair)
        work[index] += 1

    dfs(0, 0, 0, 0)
    return best


def chiitoitsu_shanten(counts: tuple[int, ...]) -> int:
    _validate_counts(counts)
    pairs = sum(1 for count in counts if count >= 2)
    unique = sum(1 for count in counts if count)
    return 6 - pairs + max(0, 7 - unique)


def kokushi_shanten(counts: tuple[int, ...]) -> int:
    _validate_counts(counts)
    unique = sum(1 for tile_id in TERMINAL_AND_HONOR_IDS if counts[tile_id])
    has_pair = any(counts[tile_id] >= 2 for tile_id in TERMINAL_AND_HONOR_IDS)
    return 13 - unique - int(has_pair)


def _validate_counts(counts: tuple[int, ...]) -> None:
    if len(counts) != TILE_COUNT:
        raise ValueError(f"counts must have {TILE_COUNT} entries")
    for tile_id, count in enumerate(counts):
        if count < 0 or count > 4:
            raise ValueError(f"invalid count for {tile_name(tile_id)}: {count}")


def _can_sequence(index: int) -> bool:
    return index < 27 and index % 9 <= 6


def _can_adjacent_wait(index: int) -> bool:
    return index < 27 and index % 9 <= 7


def _can_closed_wait(index: int) -> bool:
    return index < 27 and index % 9 <= 6
