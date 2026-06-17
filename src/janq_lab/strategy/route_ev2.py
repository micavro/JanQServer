"""Experimental deeper-EV JanQ strategy.

route_ev2 is deliberately a research copy, not a replacement for route_ev.
It keeps the reviewed route_ev policy as a safety baseline, then uses a small
finite-horizon Bellman search to override only when the EV gap is meaningful.
The current horizon is intentionally one shot plus a terminal heuristic; a full
two-shot expansion was too slow for interactive play in pure Python.
"""

from __future__ import annotations

from functools import lru_cache

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
from janq_lab.strategy.greedy import AreaDecision, DiscardDecision
from janq_lab.strategy import route_ev as base


SEARCH_DEPTH = 1
DISCARD_BEAM_WIDTH = 4
AREA_OVERRIDE_MARGIN_RATIO = 0.08
DISCARD_OVERRIDE_MARGIN_RATIO = 0.08
MIN_AREA_OVERRIDE_VALUE = 25.0
MIN_DISCARD_OVERRIDE_VALUE = 4.0


def choose_route_ev2_area(
    hand: TileSet | list[int] | tuple[int, ...],
    table: NyukyuTable,
    balls: int,
    *,
    dora_id: int | None = None,
    ura_dora_id: int | None = None,
    is_reach: bool = False,
) -> AreaDecision:
    state = hand if isinstance(hand, TileSet) else tile_set(hand)
    baseline = base.choose_route_ev_area(
        state,
        table,
        balls,
        dora_id=dora_id,
        ura_dora_id=ura_dora_id,
        is_reach=is_reach,
    )
    if balls <= 0:
        return baseline
    if _baseline_area_locked(baseline.reason):
        return AreaDecision(
            baseline.area,
            baseline.target_tiles,
            baseline.target_weight,
            f"route_ev2_keep_locked:{baseline.reason}",
            baseline.target_factors,
        )

    areas = table.areas
    scored = tuple(
        (
            _area_ev(
                state.counts,
                balls,
                area,
                SEARCH_DEPTH,
                areas,
                dora_id,
                ura_dora_id,
                is_reach,
            ),
            -area,
            area,
        )
        for area in range(1, AREA_COUNT + 1)
    )
    best_value, _, best_area = max(scored)
    baseline_value = next(value for value, _, area in scored if area == baseline.area)
    if not _beats_baseline(best_value, baseline_value, AREA_OVERRIDE_MARGIN_RATIO, MIN_AREA_OVERRIDE_VALUE):
        return AreaDecision(
            baseline.area,
            baseline.target_tiles,
            baseline.target_weight,
            f"route_ev2_keep_base:ev={baseline_value:.2f}:best={best_value:.2f}:{baseline.reason}",
            baseline.target_factors,
        )

    targets = baseline.target_tiles or improving_tiles(state) or winning_tiles(state)
    target_factors = baseline.target_factors
    target_weight = _target_weight(table, best_area, targets, target_factors)
    return AreaDecision(
        best_area,
        targets,
        target_weight,
        (
            f"route_ev2_area_depth{SEARCH_DEPTH}:ev={best_value:.2f}"
            f":base_area={baseline.area}:base_ev={baseline_value:.2f}"
            f":base={baseline.reason}"
        ),
        target_factors,
    )


def choose_route_ev2_discard(
    hand: TileSet | list[int] | tuple[int, ...],
    balls: int,
    *,
    dora_id: int | None = None,
    ura_dora_id: int | None = None,
    is_reach: bool = False,
    turn: int | None = None,
    drawn_tile: int | None = None,
) -> DiscardDecision:
    state = hand if isinstance(hand, TileSet) else tile_set(hand)
    baseline = base.choose_route_ev_discard(
        state,
        balls,
        dora_id=dora_id,
        ura_dora_id=ura_dora_id,
        is_reach=is_reach,
        turn=turn,
        drawn_tile=drawn_tile,
    )
    if baseline.is_agari or baseline.discard_tile is None:
        return baseline
    if is_reach or "yakuman_tenpai_locked" in baseline.reason or "honor_fourth_tsumogiri" in baseline.reason:
        return baseline
    if balls <= 0:
        return baseline
    if _baseline_discard_locked(baseline.reason):
        after = state.with_removed_one(baseline.discard_tile)
        return DiscardDecision(
            False,
            baseline.discard_tile,
            shanten(after),
            winning_tiles(after),
            f"route_ev2_keep_locked:{baseline.reason}",
            baseline.declare_riichi,
        )

    areas = base._default_areas()
    candidates = []
    candidate_tiles = _discard_candidate_beam(
        state,
        balls,
        areas,
        baseline_tile=baseline.discard_tile,
        drawn_tile=drawn_tile,
    )
    for tile_id in candidate_tiles:
        after = state.with_removed_one(tile_id)
        value, declare_riichi = _discard_ev(
            after.counts,
            balls,
            SEARCH_DEPTH,
            areas,
            dora_id,
            ura_dora_id,
            turn,
        )
        if tile_id == baseline.discard_tile and baseline.declare_riichi:
            value = max(
                value,
                _riichi_value_13(after.counts, balls, SEARCH_DEPTH, areas, dora_id, ura_dora_id),
            )
            declare_riichi = True
        candidates.append((value, int(declare_riichi), -tile_id, tile_id, after, declare_riichi))

    best_value, _, _, best_tile, best_after, best_riichi = max(candidates)
    baseline_value = next(
        value
        for value, _, _, tile_id, _, _ in candidates
        if tile_id == baseline.discard_tile
    )
    if not _beats_baseline(
        best_value,
        baseline_value,
        DISCARD_OVERRIDE_MARGIN_RATIO,
        MIN_DISCARD_OVERRIDE_VALUE,
    ):
        after = state.with_removed_one(baseline.discard_tile)
        return DiscardDecision(
            False,
            baseline.discard_tile,
            shanten(after),
            winning_tiles(after),
            (
                f"route_ev2_keep_base:ev={baseline_value:.2f}"
                f":best={best_value:.2f}:{baseline.reason}"
            ),
            baseline.declare_riichi,
        )

    return DiscardDecision(
        False,
        best_tile,
        shanten(best_after),
        winning_tiles(best_after),
        (
            f"route_ev2_discard_depth{SEARCH_DEPTH}:ev={best_value:.2f}"
            f":base_tile={baseline.discard_tile}:base_ev={baseline_value:.2f}"
            f":base={baseline.reason}"
        ),
        best_riichi,
    )


choose_route_ev2_area.uses_context = True  # type: ignore[attr-defined]
choose_route_ev2_area.uses_full_context = True  # type: ignore[attr-defined]
choose_route_ev2_discard.uses_context = True  # type: ignore[attr-defined]
choose_route_ev2_discard.uses_full_context = True  # type: ignore[attr-defined]


def _beats_baseline(best: float, baseline_value: float, ratio: float, minimum: float) -> bool:
    return best > baseline_value + max(minimum, abs(baseline_value) * ratio)


def _baseline_area_locked(reason: str) -> bool:
    return (
        "yakuman_route:" in reason
        or "honitsu_" in reason
        or "normal_side_" in reason
    )


def _baseline_discard_locked(reason: str) -> bool:
    return (
        "suuankou" in reason
        or "daisangen" in reason
        or "kokushi" in reason
        or "chuuren" in reason
        or "honitsu_" in reason
        or "normal_side_" in reason
    )


def _target_weight(
    table: NyukyuTable,
    area: int,
    targets: tuple[int, ...],
    target_factors: tuple[float, ...],
) -> int:
    factors = target_factors if len(target_factors) == len(targets) else (1.0,) * len(targets)
    return round(sum(table.tile_weight(area, tile_id) * factor for tile_id, factor in zip(targets, factors)))


def _discard_ev(
    counts: tuple[int, ...],
    balls: int,
    depth: int,
    areas: tuple[tuple[int, ...], ...],
    dora_id: int | None,
    ura_dora_id: int | None,
    turn: int | None,
) -> tuple[float, bool]:
    hand = TileSet(counts)
    if balls <= shanten(hand):
        return 0.0, False
    waits = winning_tiles(hand)
    if not waits:
        return _value_13(counts, balls, depth, areas, dora_id, ura_dora_id, False), False

    dama_value = _value_13(counts, balls, depth, areas, dora_id, ura_dora_id, False)
    riichi_value = _riichi_value_13(counts, balls, depth, areas, dora_id, ura_dora_id)
    if turn == 1:
        riichi_value *= 1.03
    if riichi_value >= dama_value:
        return riichi_value, True
    return dama_value, False


@lru_cache(maxsize=250_000)
def _value_13(
    counts: tuple[int, ...],
    balls: int,
    depth: int,
    areas: tuple[tuple[int, ...], ...],
    dora_id: int | None,
    ura_dora_id: int | None,
    is_reach: bool,
) -> float:
    if balls <= 0:
        return 0.0
    hand = TileSet(counts)
    if not is_reach and balls <= shanten(hand):
        return 0.0
    if depth <= 0:
        return _heuristic_value_13(counts, balls, areas)
    return max(
        _area_ev(counts, balls, area, depth, areas, dora_id, ura_dora_id, is_reach)
        for area in range(1, AREA_COUNT + 1)
    )


@lru_cache(maxsize=250_000)
def _value_14(
    counts: tuple[int, ...],
    balls: int,
    depth: int,
    areas: tuple[tuple[int, ...], ...],
    dora_id: int | None,
    ura_dora_id: int | None,
    is_reach: bool,
    drawn_tile: int | None,
) -> float:
    hand = TileSet(counts)
    if is_complete_hand(hand):
        return _complete_value(counts, dora_id, ura_dora_id, is_reach)
    if balls <= 0:
        return 0.0
    if is_reach:
        if drawn_tile is None or hand.counts[drawn_tile] <= 0:
            return 0.0
        return _value_13(
            hand.with_removed_one(drawn_tile).counts,
            balls,
            depth,
            areas,
            dora_id,
            ura_dora_id,
            True,
        )

    best = 0.0
    for tile_id in _recursive_discard_candidates(hand):
        after = hand.with_removed_one(tile_id)
        if balls <= shanten(after):
            candidate = 0.0
        else:
            candidate = _value_13(after.counts, balls, depth, areas, dora_id, ura_dora_id, False)
        if candidate > best:
            best = candidate
    return best


@lru_cache(maxsize=250_000)
def _riichi_value_13(
    counts: tuple[int, ...],
    balls: int,
    depth: int,
    areas: tuple[tuple[int, ...], ...],
    dora_id: int | None,
    ura_dora_id: int | None,
) -> float:
    return _value_13(counts, balls, depth, areas, dora_id, ura_dora_id, True)


@lru_cache(maxsize=500_000)
def _area_ev(
    counts: tuple[int, ...],
    balls: int,
    area: int,
    depth: int,
    areas: tuple[tuple[int, ...], ...],
    dora_id: int | None,
    ura_dora_id: int | None,
    is_reach: bool,
) -> float:
    del depth, dora_id, ura_dora_id, is_reach
    return base._area_expectation_fast(counts, balls, area, areas)


def _heuristic_value_13(
    counts: tuple[int, ...],
    balls: int,
    areas: tuple[tuple[int, ...], ...],
) -> float:
    if balls <= shanten(TileSet(counts)):
        return 0.0
    return base._heuristic_value_13(counts, balls, areas)


@lru_cache(maxsize=250_000)
def _complete_value(
    counts: tuple[int, ...],
    dora_id: int | None,
    ura_dora_id: int | None,
    is_reach: bool,
) -> float:
    return base._complete_value_context(
        counts,
        dora_id=dora_id,
        ura_dora_id=ura_dora_id,
        reach=is_reach,
        double_reach=False,
        ippatsu=False,
    )


def _discard_candidate_beam(
    hand: TileSet,
    balls: int,
    areas: tuple[tuple[int, ...], ...],
    *,
    baseline_tile: int,
    drawn_tile: int | None,
) -> tuple[int, ...]:
    scored = []
    for tile_id in discard_options(hand):
        after = hand.with_removed_one(tile_id)
        after_shanten = shanten(after)
        score = (
            int(tile_id == baseline_tile),
            -after_shanten,
            len(improving_tiles(after)),
            int(drawn_tile is not None and tile_id == drawn_tile),
            -tile_id,
        )
        scored.append((score, tile_id))
    scored.sort(reverse=True)
    selected = [baseline_tile]
    if drawn_tile is not None and drawn_tile not in selected and hand.counts[drawn_tile] > 0:
        selected.append(drawn_tile)
    for _, tile_id in scored:
        if tile_id not in selected:
            selected.append(tile_id)
        if len(selected) >= DISCARD_BEAM_WIDTH:
            break
    return tuple(tile_id for tile_id in selected if hand.counts[tile_id] > 0)


@lru_cache(maxsize=250_000)
def _recursive_discard_candidates(hand: TileSet) -> tuple[int, ...]:
    scored = []
    for tile_id in discard_options(hand):
        after = hand.with_removed_one(tile_id)
        scored.append((shanten(after), -len(improving_tiles(after)), tile_id))
    if not scored:
        return ()
    best_shanten = min(row[0] for row in scored)
    candidates = [tile_id for after_shanten, _, tile_id in scored if after_shanten <= best_shanten + 1]
    if len(candidates) <= 10:
        return tuple(candidates)
    scored_candidates = [row for row in scored if row[2] in candidates]
    scored_candidates.sort()
    return tuple(tile_id for _, _, tile_id in scored_candidates[:10])
