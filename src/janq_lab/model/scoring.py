"""JanQ hand scoring under the public SEGA help rules.

The scorer intentionally models JanQ's closed-hand, self-draw-only rule set:
no calls, no kan, no red dora, no fu, East round only, and no player wind.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

from janq_lab.model.hand import TERMINAL_AND_HONOR_IDS, TileSet, tile_set
from janq_lab.tiles import TILE_COUNT, check_tile_id


MAN = frozenset(range(0, 9))
SOU = frozenset(range(9, 18))
PIN = frozenset(range(18, 27))
SUITS = (MAN, SOU, PIN)
HONORS = frozenset(range(27, 34))
WINDS = frozenset((27, 28, 29, 30))
VALUE_HONORS = frozenset((27, 31, 32, 33))
DRAGONS = frozenset((31, 32, 33))
GREEN = frozenset((10, 11, 12, 14, 16, 32))


@dataclass(frozen=True)
class Meld:
    kind: str
    tile: int

    @property
    def tiles(self) -> tuple[int, ...]:
        if self.kind == "sequence":
            return (self.tile, self.tile + 1, self.tile + 2)
        return (self.tile, self.tile, self.tile)


@dataclass(frozen=True)
class StandardShape:
    pair: int
    melds: tuple[Meld, ...]


@dataclass(frozen=True)
class JanqScore:
    han: int
    yakuman_count: int
    yaku: tuple[str, ...]

    @property
    def is_yakuman(self) -> bool:
        return self.yakuman_count > 0

    @property
    def yaku_level(self) -> str:
        if self.yakuman_count:
            return "YL_YAKUMAN"
        if self.han <= 0:
            return "YL_NONE"
        if self.han == 1:
            return "YL_01HAN"
        if self.han == 2:
            return "YL_02HAN"
        if self.han == 3:
            return "YL_03HAN"
        if self.han <= 5:
            return "YL_04HAN"
        if self.han <= 7:
            return "YL_06HAN"
        if self.han <= 10:
            return "YL_08HAN"
        if self.han <= 12:
            return "YL_11HAN"
        return "YL_YAKUMAN"


def score_hand(
    tiles: Iterable[int] | TileSet,
    *,
    dora_id: int | None = None,
    ura_dora_id: int | None = None,
    reach: bool = False,
    double_reach: bool = False,
    ippatsu: bool = False,
    tenhou: bool = False,
    tsumo: bool = True,
) -> JanqScore:
    """Score a complete JanQ hand and return the highest-valued interpretation."""

    hand = tiles if isinstance(tiles, TileSet) else tile_set(tiles)
    if hand.size != 14:
        raise ValueError(f"scoring needs a 14-tile hand, got {hand.size}")
    if dora_id is not None:
        check_tile_id(dora_id)
    if ura_dora_id is not None:
        check_tile_id(ura_dora_id)

    yakuman = _natural_yakuman(hand.counts, tenhou=tenhou)
    if yakuman:
        names = tuple(name for name, _ in yakuman)
        count = min(4, sum(value for _, value in yakuman))
        return JanqScore(han=0, yakuman_count=count, yaku=names)

    candidates: list[JanqScore] = []
    if _is_kokushi_complete(hand.counts):
        candidates.append(_non_natural_yakuman_or_score(
            hand,
            base_han=0,
            yaku=("kokushi",),
            dora_id=dora_id,
            ura_dora_id=ura_dora_id,
            reach=reach,
            double_reach=double_reach,
            ippatsu=ippatsu,
            tenhou=tenhou,
            tsumo=tsumo,
        ))
    if _is_chiitoitsu_complete(hand.counts):
        candidates.append(_score_chiitoitsu(
            hand,
            dora_id=dora_id,
            ura_dora_id=ura_dora_id,
            reach=reach,
            double_reach=double_reach,
            ippatsu=ippatsu,
            tenhou=tenhou,
            tsumo=tsumo,
        ))

    for shape in standard_shapes(hand):
        candidates.append(_score_standard(
            hand,
            shape,
            dora_id=dora_id,
            ura_dora_id=ura_dora_id,
            reach=reach,
            double_reach=double_reach,
            ippatsu=ippatsu,
            tenhou=tenhou,
            tsumo=tsumo,
        ))

    if not candidates:
        return JanqScore(han=0, yakuman_count=0, yaku=())

    return max(candidates, key=lambda score: (score.yakuman_count, score.han, len(score.yaku)))


def standard_shapes(tiles: Iterable[int] | TileSet) -> tuple[StandardShape, ...]:
    hand = tiles if isinstance(tiles, TileSet) else tile_set(tiles)
    if hand.size != 14:
        return ()
    return _standard_shapes_cached(hand.counts)


@lru_cache(maxsize=500_000)
def _standard_shapes_cached(counts: tuple[int, ...]) -> tuple[StandardShape, ...]:
    shapes: list[StandardShape] = []
    for pair, count in enumerate(counts):
        if count < 2:
            continue
        work = list(counts)
        work[pair] -= 2
        for melds in _meld_decompositions_cached(tuple(work)):
            shapes.append(StandardShape(pair=pair, melds=melds))
    return tuple(shapes)


@lru_cache(maxsize=500_000)
def _meld_decompositions_cached(counts: tuple[int, ...]) -> tuple[tuple[Meld, ...], ...]:
    try:
        tile_id = next(i for i, count in enumerate(counts) if count)
    except StopIteration:
        return ((),)

    results: list[tuple[Meld, ...]] = []
    if counts[tile_id] >= 3:
        work = list(counts)
        work[tile_id] -= 3
        for rest in _meld_decompositions_cached(tuple(work)):
            results.append((Meld("triplet", tile_id),) + rest)

    if _can_sequence(tile_id) and counts[tile_id + 1] and counts[tile_id + 2]:
        work = list(counts)
        work[tile_id] -= 1
        work[tile_id + 1] -= 1
        work[tile_id + 2] -= 1
        for rest in _meld_decompositions_cached(tuple(work)):
            results.append((Meld("sequence", tile_id),) + rest)

    return tuple(results)


def _natural_yakuman(counts: tuple[int, ...], *, tenhou: bool) -> tuple[tuple[str, int], ...]:
    yaku: list[tuple[str, int]] = []
    hand = TileSet(counts)
    shapes = standard_shapes(hand)

    if tenhou:
        yaku.append(("tenhou", 1))
    if _is_kokushi_complete(counts):
        yaku.append(("kokushi", 1))
    if all(counts[tile_id] == 0 for tile_id in range(TILE_COUNT) if tile_id not in HONORS):
        yaku.append(("tsuuiisou", 1))
    if all(counts[tile_id] == 0 for tile_id in range(TILE_COUNT) if tile_id not in GREEN):
        yaku.append(("ryuuiisou", 1))
    if all(counts[tile_id] == 0 for tile_id in range(TILE_COUNT) if not _is_terminal(tile_id)):
        yaku.append(("chinroutou", 1))
    if _is_chuuren(counts):
        yaku.append(("chuuren", 1))

    best_shape_yaku: tuple[tuple[str, int], ...] = ()
    for shape in shapes:
        current: list[tuple[str, int]] = []
        triplets = {meld.tile for meld in shape.melds if meld.kind == "triplet"}
        if len(triplets) == 4:
            current.append(("suuankou", 1))
        if DRAGONS.issubset(triplets):
            current.append(("daisangen", 1))
        wind_triplets = WINDS.intersection(triplets)
        if len(wind_triplets) == 4:
            current.append(("daisuushi", 1))
        elif len(wind_triplets) == 3 and shape.pair in WINDS - wind_triplets:
            current.append(("shousuushi", 1))
        if sum(value for _, value in current) > sum(value for _, value in best_shape_yaku):
            best_shape_yaku = tuple(current)

    yaku.extend(best_shape_yaku)
    return tuple(yaku)


def _score_chiitoitsu(
    hand: TileSet,
    *,
    dora_id: int | None,
    ura_dora_id: int | None,
    reach: bool,
    double_reach: bool,
    ippatsu: bool,
    tenhou: bool,
    tsumo: bool,
) -> JanqScore:
    han = 2
    yaku = ["chiitoitsu"]
    if all(count == 0 or tile_id in TERMINAL_AND_HONOR_IDS for tile_id, count in enumerate(hand.counts)):
        han += 2
        yaku.append("chanta")
    return _non_natural_yakuman_or_score(
        hand,
        base_han=han,
        yaku=tuple(yaku),
        dora_id=dora_id,
        ura_dora_id=ura_dora_id,
        reach=reach,
        double_reach=double_reach,
        ippatsu=ippatsu,
        tenhou=tenhou,
        tsumo=tsumo,
    )


def _score_standard(
    hand: TileSet,
    shape: StandardShape,
    *,
    dora_id: int | None,
    ura_dora_id: int | None,
    reach: bool,
    double_reach: bool,
    ippatsu: bool,
    tenhou: bool,
    tsumo: bool,
) -> JanqScore:
    han = 0
    yaku: list[str] = []
    melds = shape.melds
    sequences = [meld for meld in melds if meld.kind == "sequence"]
    triplets = [meld for meld in melds if meld.kind == "triplet"]

    if all(_is_simple(tile_id) for tile_id, count in enumerate(hand.counts) for _ in range(count)):
        han += 1
        yaku.append("tanyao")

    if len(sequences) == 4 and shape.pair not in VALUE_HONORS:
        han += 1
        yaku.append("pinfu")

    seq_counts: dict[tuple[int, int], int] = {}
    for meld in sequences:
        seq_counts[(_suit_index(meld.tile), _rank(meld.tile))] = (
            seq_counts.get((_suit_index(meld.tile), _rank(meld.tile)), 0) + 1
        )
    duplicate_sequences = sum(count // 2 for count in seq_counts.values())
    if duplicate_sequences >= 2:
        han += 3
        yaku.append("ryanpeikou")
    elif duplicate_sequences == 1:
        han += 1
        yaku.append("iipeikou")

    for triplet in triplets:
        if triplet.tile == 27:
            han += 1
            yaku.append("ton")
        elif triplet.tile == 31:
            han += 1
            yaku.append("haku")
        elif triplet.tile == 32:
            han += 1
            yaku.append("hatsu")
        elif triplet.tile == 33:
            han += 1
            yaku.append("chun")

    if _has_chinitsu(hand.counts):
        han += 6
        yaku.append("chinitsu")
    elif _has_honitsu(hand.counts):
        han += 3
        yaku.append("honitsu")

    if _is_junchan(shape):
        han += 3
        yaku.append("junchan")
    elif _is_chanta(shape):
        han += 2
        yaku.append("chanta")

    if _has_sanshoku_doujun(sequences):
        han += 2
        yaku.append("sanshoku_doujun")
    if _has_ittsu(sequences):
        han += 2
        yaku.append("ittsu")
    if _has_sanshoku_doukou(triplets):
        han += 2
        yaku.append("sanshoku_doukou")
    if len(triplets) >= 3:
        han += 2
        yaku.append("sanankou")
    if _has_shousangen(shape, triplets):
        han += 2
        yaku.append("shousangen")

    return _non_natural_yakuman_or_score(
        hand,
        base_han=han,
        yaku=tuple(yaku),
        dora_id=dora_id,
        ura_dora_id=ura_dora_id,
        reach=reach,
        double_reach=double_reach,
        ippatsu=ippatsu,
        tenhou=tenhou,
        tsumo=tsumo,
    )


def _non_natural_yakuman_or_score(
    hand: TileSet,
    *,
    base_han: int,
    yaku: tuple[str, ...],
    dora_id: int | None,
    ura_dora_id: int | None,
    reach: bool,
    double_reach: bool,
    ippatsu: bool,
    tenhou: bool,
    tsumo: bool,
) -> JanqScore:
    han = base_han
    names = list(yaku)
    if tenhou:
        return JanqScore(han=0, yakuman_count=1, yaku=("tenhou",))
    if tsumo:
        han += 1
        names.append("tsumo")
    if double_reach:
        han += 2
        names.append("double_reach")
    elif reach:
        han += 1
        names.append("reach")
    if ippatsu and (reach or double_reach):
        han += 1
        names.append("ippatsu")
    if dora_id is not None and hand.counts[dora_id]:
        han += hand.counts[dora_id]
        names.extend(("dora",) * hand.counts[dora_id])
    if (reach or double_reach) and ura_dora_id is not None and hand.counts[ura_dora_id]:
        han += hand.counts[ura_dora_id]
        names.extend(("ura_dora",) * hand.counts[ura_dora_id])

    if han >= 13:
        return JanqScore(han=han, yakuman_count=1, yaku=tuple(names + ["kazoe_yakuman"]))
    return JanqScore(han=han, yakuman_count=0, yaku=tuple(names))


def _is_chiitoitsu_complete(counts: tuple[int, ...]) -> bool:
    return sum(1 for count in counts if count == 2) == 7


def _is_kokushi_complete(counts: tuple[int, ...]) -> bool:
    return all(counts[tile_id] for tile_id in TERMINAL_AND_HONOR_IDS) and any(
        counts[tile_id] >= 2 for tile_id in TERMINAL_AND_HONOR_IDS
    )


def _can_sequence(tile_id: int) -> bool:
    return tile_id < 27 and tile_id % 9 <= 6


def _suit_index(tile_id: int) -> int:
    return tile_id // 9


def _rank(tile_id: int) -> int:
    return tile_id % 9 + 1


def _is_terminal(tile_id: int) -> bool:
    return tile_id < 27 and tile_id % 9 in (0, 8)


def _is_simple(tile_id: int) -> bool:
    return tile_id < 27 and tile_id % 9 not in (0, 8)


def _has_chinitsu(counts: tuple[int, ...]) -> bool:
    return sum(bool(any(counts[tile_id] for tile_id in suit)) for suit in SUITS) == 1 and not any(
        counts[tile_id] for tile_id in HONORS
    )


def _has_honitsu(counts: tuple[int, ...]) -> bool:
    return sum(bool(any(counts[tile_id] for tile_id in suit)) for suit in SUITS) == 1 and any(
        counts[tile_id] for tile_id in HONORS
    )


def _is_chanta(shape: StandardShape) -> bool:
    return _set_has_terminal_or_honor((shape.pair, shape.pair)) and all(
        _set_has_terminal_or_honor(meld.tiles) for meld in shape.melds
    ) and any(meld.kind == "sequence" for meld in shape.melds)


def _is_junchan(shape: StandardShape) -> bool:
    return _set_has_terminal((shape.pair, shape.pair)) and all(
        _set_has_terminal(meld.tiles) for meld in shape.melds
    ) and all(all(tile_id not in HONORS for tile_id in meld.tiles) for meld in shape.melds)


def _set_has_terminal_or_honor(tiles: tuple[int, ...]) -> bool:
    return any(tile_id in HONORS or _is_terminal(tile_id) for tile_id in tiles)


def _set_has_terminal(tiles: tuple[int, ...]) -> bool:
    return any(_is_terminal(tile_id) for tile_id in tiles) and all(tile_id not in HONORS for tile_id in tiles)


def _has_sanshoku_doujun(sequences: list[Meld]) -> bool:
    keys = {(_suit_index(meld.tile), _rank(meld.tile)) for meld in sequences}
    for rank in range(1, 8):
        if all((suit, rank) in keys for suit in range(3)):
            return True
    return False


def _has_ittsu(sequences: list[Meld]) -> bool:
    keys = {(_suit_index(meld.tile), _rank(meld.tile)) for meld in sequences}
    for suit in range(3):
        if all((suit, rank) in keys for rank in (1, 4, 7)):
            return True
    return False


def _has_sanshoku_doukou(triplets: list[Meld]) -> bool:
    keys = {(_suit_index(meld.tile), _rank(meld.tile)) for meld in triplets if meld.tile < 27}
    for rank in range(1, 10):
        if all((suit, rank) in keys for suit in range(3)):
            return True
    return False


def _has_shousangen(shape: StandardShape, triplets: list[Meld]) -> bool:
    dragon_triplets = {meld.tile for meld in triplets if meld.tile in DRAGONS}
    return len(dragon_triplets) == 2 and shape.pair in DRAGONS - dragon_triplets


def _is_chuuren(counts: tuple[int, ...]) -> bool:
    for suit_start in (0, 9, 18):
        suit_ids = range(suit_start, suit_start + 9)
        if sum(counts[tile_id] for tile_id in suit_ids) != 14:
            continue
        if any(counts[tile_id] for tile_id in range(TILE_COUNT) if tile_id not in suit_ids):
            continue
        needed = [3, 1, 1, 1, 1, 1, 1, 1, 3]
        if all(counts[suit_start + i] >= needed[i] for i in range(9)):
            return True
    return False
