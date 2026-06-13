"""Route-aware EV-ish JanQ strategy.

This is a practical first pass at the mixed strategy described in the notes:
yakuman routes first, honitsu second, normal hand efficiency last. Decisions use
a shallow Bellman search, so area selection can prefer a multi-shot route over
the immediate best wait when the future route value is higher.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from math import comb

from janq_lab.assets.nyukyu import AREA_COUNT, EXPECTED_WEIGHT_SUM, NyukyuTable
from janq_lab.model.economy import payout_for_score
from janq_lab.model.hand import (
    TERMINAL_AND_HONOR_IDS,
    TileSet,
    discard_options,
    improving_tiles,
    is_complete_hand,
    shanten,
    tile_set,
    winning_tiles,
)
from janq_lab.model.scoring import GREEN, HONORS, PIN, SOU, JanqScore, score_hand
from janq_lab.strategy.greedy import AreaDecision, DiscardDecision, choose_area_for_targets
from janq_lab.strategy.public import choose_public_area
from janq_lab.tiles import TILE_COUNT


MAN = frozenset(range(0, 9))
SUITS = (MAN, SOU, PIN)
DRAGONS = (31, 32, 33)
YAKUMAN_COMPLETE_VALUE = 220.0
COUNTED_YAKUMAN_COMPLETE_VALUE = 170.0
DASANGEN_ROUTE_VALUE = 360.0
SUUANKOU_ROUTE_VALUE = 330.0
CHUUREN_ROUTE_VALUE = 300.0
KOKUSHI_ROUTE_VALUE = 60.0
SEARCH_DEPTH = 1


@dataclass(frozen=True)
class RouteEstimate:
    name: str
    reward: float
    missing: int
    targets: tuple[int, ...]
    probability: float

    @property
    def value(self) -> float:
        return self.reward * self.probability


@dataclass(frozen=True)
class TenpaiDiscardEstimate:
    discard: int
    waits: tuple[int, ...]
    win_probability: float
    value: float
    declare_riichi: bool


@dataclass(frozen=True)
class NextAreaEstimate:
    area: int
    targets: tuple[int, ...]
    progress_probability: float
    protection_probability: float
    score: float


def choose_route_ev_area(
    hand: TileSet | list[int] | tuple[int, ...],
    table: NyukyuTable,
    balls: int,
    *,
    dora_id: int | None = None,
    ura_dora_id: int | None = None,
    is_reach: bool = False,
) -> AreaDecision:
    state = hand if isinstance(hand, TileSet) else tile_set(hand)
    yakuman_waits = _yakuman_waits(state.counts)
    if yakuman_waits:
        return choose_area_for_targets(table, yakuman_waits, "yakuman_tenpai_locked")

    winners = winning_tiles(state)
    if winners and is_reach:
        return choose_area_for_targets(table, winners, "riichi_locked_wait")
    if winners and balls <= 3:
        return choose_area_for_targets(table, winners, f"normal_tenpai_keep:b={balls}")
    if winners:
        improve_targets = _normal_tenpai_improvement_targets(
            state,
            balls,
            table.areas,
            dora_id=dora_id,
            ura_dora_id=ura_dora_id,
        )
        if improve_targets:
            return choose_area_for_targets(
                table,
                improve_targets,
                f"normal_tenpai_improve:b={balls}",
            )
        return choose_area_for_targets(table, winners, f"normal_tenpai_keep:no_improve:b={balls}")

    active_route = _active_route(state.counts, balls, table.areas)
    if active_route is None:
        targets = improving_tiles(state)
        if targets:
            return _area_decision_from_estimate(
                table,
                _next_area_estimate(state.counts, targets, table.areas),
                "normal_next_area",
            )
        return choose_public_area(state, table)
    if active_route.name.startswith("honitsu_"):
        targets = _honitsu_progress_targets(state, active_route.name)
        if targets:
            return _area_decision_from_estimate(
                table,
                _next_area_estimate(state.counts, targets, table.areas),
                f"{active_route.name}_next_area",
            )
        return choose_public_area(state, table)
    estimate = _next_area_estimate(
        state.counts,
        active_route.targets,
        table.areas,
        winners=winners,
    )
    return _area_decision_from_estimate(
        table,
        estimate,
        f"yakuman_route:{active_route.name}",
        win_probability=_conditioned_area_probability(
            state.counts,
            winners,
            estimate.area,
            table.areas,
        ),
    )


def choose_route_ev_discard(
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
    if is_reach:
        if is_complete_hand(state):
            return DiscardDecision(True, None, None, (), "complete_hand:riichi")
        discard = drawn_tile if drawn_tile is not None and state.counts[drawn_tile] else _least_route_value_discard(state)
        after = state.with_removed_one(discard)
        return DiscardDecision(
            False,
            discard,
            shanten(after),
            winning_tiles(after),
            "riichi_locked_tsumogiri",
        )
    if is_complete_hand(state):
        return DiscardDecision(True, None, None, (), "complete_hand")
    if balls <= 0:
        discard = _least_route_value_discard(state)
        return DiscardDecision(False, discard, None, (), "no_balls")

    areas = _default_areas()
    yakuman_tenpai_discard = _yakuman_tenpai_discard(state, areas)
    if yakuman_tenpai_discard is not None:
        discard, accepts = yakuman_tenpai_discard
        after = state.with_removed_one(discard)
        return DiscardDecision(
            False,
            discard,
            shanten(after),
            accepts,
            "route_ev_discard:yakuman_tenpai_locked",
            declare_riichi=True,
        )

    route = _active_route(state.counts, balls, _default_areas())
    tenpai_discard = _normal_tenpai_discard(
        state,
        balls,
        areas,
        dora_id=dora_id,
        ura_dora_id=ura_dora_id,
        turn=turn,
    )
    if tenpai_discard is not None and _should_keep_normal_tenpai(tenpai_discard.value, route, balls):
        after = state.with_removed_one(tenpai_discard.discard)
        reason = (
            "route_ev_discard:normal_tenpai"
            f":win_p={tenpai_discard.win_probability:.3f}"
            f":v={tenpai_discard.value:.2f}"
        )
        if tenpai_discard.declare_riichi:
            reason = f"{reason}:riichi"
        else:
            reason = f"{reason}:dama_improve_window"
        return DiscardDecision(
            False,
            tenpai_discard.discard,
            shanten(after),
            tenpai_discard.waits,
            reason,
            declare_riichi=tenpai_discard.declare_riichi,
        )

    if route is None:
        discard, next_area = _normal_lookahead_discard(state, balls, areas)
        after = state.with_removed_one(discard)
        return DiscardDecision(
            False,
            discard,
            shanten(after),
            winning_tiles(after),
            (
                "route_ev_discard:normal_next_area"
                f":area={next_area.area}:p={next_area.progress_probability:.3f}"
                f":protect={next_area.protection_probability:.3f}"
            ),
        )
    if route.name.startswith("honitsu_"):
        discard, next_area = _honitsu_lookahead_discard(state, balls, route, areas)
        after = state.with_removed_one(discard)
        return DiscardDecision(
            False,
            discard,
            shanten(after),
            winning_tiles(after),
            (
                f"route_ev_discard:{route.name}:next_area={next_area.area}"
                f":p={next_area.progress_probability:.3f}"
                f":protect={next_area.protection_probability:.3f}"
            ),
        )

    discard, next_area = _yakuman_lookahead_discard(state, balls, route, areas)
    after = state.with_removed_one(discard)
    reason = (
        f"route_ev_discard:{route.name}:next_area={next_area.area}"
        f":p={next_area.progress_probability:.3f}"
        f":protect={next_area.protection_probability:.3f}"
    )
    side_suit = _side_route_suit(state.counts, route)
    if side_suit is not None and route.name in {"suuankou", "daisangen"}:
        reason = f"{reason}:side_{_suit_name(side_suit)}"
    return DiscardDecision(
        False,
        discard,
        shanten(after),
        winning_tiles(after),
        reason,
    )


choose_route_ev_area.uses_context = True  # type: ignore[attr-defined]
choose_route_ev_area.uses_full_context = True  # type: ignore[attr-defined]
choose_route_ev_discard.uses_context = True  # type: ignore[attr-defined]
choose_route_ev_discard.uses_full_context = True  # type: ignore[attr-defined]


def _area_decision_from_estimate(
    table: NyukyuTable,
    estimate: NextAreaEstimate,
    reason: str,
    *,
    win_probability: float = 0.0,
) -> AreaDecision:
    return AreaDecision(
        area=estimate.area,
        target_tiles=estimate.targets,
        target_weight=sum(
            table.tile_weight(estimate.area, tile_id)
            for tile_id in estimate.targets
        ),
        reason=(
            f"{reason}:progress={estimate.progress_probability:.3f}"
            f":win={win_probability:.3f}"
            f":protect={estimate.protection_probability:.3f}"
            f":v={estimate.score:.3f}"
        ),
    )


def _next_area_estimate(
    counts: tuple[int, ...],
    targets: tuple[int, ...],
    areas: tuple[tuple[int, ...], ...],
    *,
    winners: tuple[int, ...] = (),
) -> NextAreaEstimate:
    protected = tuple(tile_id for tile_id, count in enumerate(counts) if count == 3)
    scored = []
    for area in range(1, AREA_COUNT + 1):
        progress_p = _conditioned_area_probability(counts, targets, area, areas)
        win_p = _conditioned_area_probability(counts, winners, area, areas)
        protect_p = _conditioned_area_probability(counts, protected, area, areas)
        score = progress_p + win_p * 0.35 + protect_p * 0.25
        scored.append((score, progress_p, protect_p, -area, area))
    score, progress_p, protect_p, _, area = max(scored)
    return NextAreaEstimate(
        area=area,
        targets=targets,
        progress_probability=progress_p,
        protection_probability=protect_p,
        score=score,
    )


def _area_expectation(
    counts: tuple[int, ...],
    balls: int,
    area: int,
    depth: int,
    areas: tuple[tuple[int, ...], ...],
) -> float:
    if balls <= 0:
        return 0.0
    weights = areas[area - 1]
    valid_total = sum(weight for tile_id, weight in enumerate(weights) if counts[tile_id] < 4)
    if valid_total <= 0:
        return 0.0

    total = 0.0
    for tile_id, weight in enumerate(weights):
        if weight <= 0 or counts[tile_id] >= 4:
            continue
        next_counts = list(counts)
        fourth_copy = counts[tile_id] == 3
        next_counts[tile_id] += 1
        next_balls = balls if fourth_copy else balls - 1
        total += (weight / valid_total) * _value_14(tuple(next_counts), next_balls, depth, areas)
    return total


def _area_expectation_fast(
    counts: tuple[int, ...],
    balls: int,
    area: int,
    areas: tuple[tuple[int, ...], ...],
) -> float:
    if balls <= 0:
        return 0.0
    weights = areas[area - 1]
    valid_total = sum(weight for tile_id, weight in enumerate(weights) if counts[tile_id] < 4)
    if valid_total <= 0:
        return 0.0

    total = 0.0
    for tile_id, weight in enumerate(weights):
        if weight <= 0 or counts[tile_id] >= 4:
            continue
        next_counts = list(counts)
        fourth_copy = counts[tile_id] == 3
        next_counts[tile_id] += 1
        next_balls = balls if fourth_copy else balls - 1
        total += (weight / valid_total) * _heuristic_value_14(tuple(next_counts), next_balls, areas)
    return total


@lru_cache(maxsize=600_000)
def _value_13(
    counts: tuple[int, ...],
    balls: int,
    depth: int,
    areas: tuple[tuple[int, ...], ...],
) -> float:
    if balls <= 0:
        return 0.0
    if depth <= 0:
        return _heuristic_value_13(counts, balls, areas)
    return max(
        _area_expectation(counts, balls, area, depth - 1, areas)
        for area in range(1, AREA_COUNT + 1)
    )


@lru_cache(maxsize=600_000)
def _value_14(
    counts: tuple[int, ...],
    balls: int,
    depth: int,
    areas: tuple[tuple[int, ...], ...],
) -> float:
    hand = TileSet(counts)
    if is_complete_hand(hand):
        return _complete_value(counts)
    if balls <= 0:
        return 0.0
    if depth <= 0:
        return _heuristic_value_14(counts, balls, areas)
    return max(
        _value_13(hand.with_removed_one(tile_id).counts, balls, depth - 1, areas)
        for tile_id in discard_options(hand)
    )


@lru_cache(maxsize=600_000)
def _complete_value(counts: tuple[int, ...]) -> float:
    score = score_hand(TileSet(counts))
    payout = float(payout_for_score(score, bet=10))
    if score.yakuman_count:
        if score.han >= 13 and "kazoe_yakuman" in score.yaku:
            return max(COUNTED_YAKUMAN_COMPLETE_VALUE, payout + 90.0)
        return max(YAKUMAN_COMPLETE_VALUE * min(4, score.yakuman_count), payout + 120.0)
    return payout + _paren_entry_value(score)


def _paren_entry_value(score: JanqScore) -> float:
    if score.han >= 11:
        return 30.0
    if score.han >= 8:
        return 22.0
    if score.han >= 6:
        return 16.0
    if score.han >= 4:
        return 10.0
    if score.han >= 2:
        return 6.0
    return 3.0


@lru_cache(maxsize=600_000)
def _heuristic_value_14(
    counts: tuple[int, ...],
    balls: int,
    areas: tuple[tuple[int, ...], ...],
) -> float:
    hand = TileSet(counts)
    if is_complete_hand(hand):
        return _complete_value(counts)
    return max(
        _heuristic_value_13(hand.with_removed_one(tile_id).counts, balls, areas)
        for tile_id in discard_options(hand)
    )


@lru_cache(maxsize=600_000)
def _heuristic_value_13(
    counts: tuple[int, ...],
    balls: int,
    areas: tuple[tuple[int, ...], ...],
) -> float:
    routes = _route_estimates(counts, balls, areas)
    best_route_value = max((route.value for route in routes), default=0.0)
    normal = _normal_efficiency_value(counts, balls, areas)
    protection = _best_protection_bonus(counts, areas) * min(3, balls) * 0.4
    return max(best_route_value, normal) + protection


@lru_cache(maxsize=600_000)
def _route_estimates(
    counts: tuple[int, ...],
    balls: int,
    areas: tuple[tuple[int, ...], ...],
) -> tuple[RouteEstimate, ...]:
    estimates = [
        _suuankou_estimate(counts, balls, areas),
        _daisangen_estimate(counts, balls, areas),
        _kokushi_estimate(counts, balls, areas),
    ]
    estimates.extend(_chuuren_estimate(counts, balls, areas, suit) for suit in SUITS)
    estimates.extend(_honitsu_estimate(counts, balls, areas, suit) for suit in SUITS)
    return tuple(route for route in estimates if route is not None)


def _best_route(
    counts: tuple[int, ...],
    balls: int,
    areas: tuple[tuple[int, ...], ...],
) -> RouteEstimate:
    estimates = _route_estimates(counts, balls, areas)
    if not estimates:
        return RouteEstimate("normal", 0.0, 0, winning_tiles(TileSet(counts)), 0.0)
    return max(estimates, key=lambda route: route.value)


@lru_cache(maxsize=600_000)
def _active_route(
    counts: tuple[int, ...],
    balls: int,
    areas: tuple[tuple[int, ...], ...],
) -> RouteEstimate | None:
    estimates = _route_estimates(counts, balls, areas)
    yakuman_routes = [
        route for route in estimates
        if not route.name.startswith("honitsu_")
    ]
    yakuman_routes.sort(key=lambda route: route.value, reverse=True)
    for route in yakuman_routes:
        if route.name == "kokushi" and route.missing > 4:
            if route.missing <= 5 and route.probability >= 0.004:
                return route
            continue
        if route.name == "daisangen":
            dragon_count = sum(counts[tile_id] for tile_id in DRAGONS)
            if dragon_count >= 3 and route.missing <= 9 and route.probability >= 0.001:
                return route
            if dragon_count >= 4 and route.missing <= 7 and route.probability >= 0.001:
                return route
            continue
        if route.name == "suuankou":
            pairish = sum(1 for count in counts if count >= 2)
            triplets = sum(1 for count in counts if count >= 3)
            pairs = sum(1 for count in counts if count == 2)
            if triplets >= 1 and pairs >= 2 and route.missing <= 7 and route.probability >= 0.002:
                return route
            if pairish >= 4 and route.missing <= 7 and route.probability >= 0.003:
                return route
            if pairish >= 3 and triplets >= 1 and route.missing <= 6 and route.probability >= 0.004:
                return route
            continue
        if route.name.startswith("chuuren_"):
            suit = _route_suit(route.name)
            suit_count = sum(counts[tile_id] for tile_id in suit)
            terminal_count = sum(counts[tile_id] for tile_id in suit if tile_id % 9 in (0, 8))
            if suit_count >= 8 and terminal_count >= 2 and route.missing <= 6 and route.probability >= 0.006:
                return route
            if suit_count >= 7 and terminal_count >= 3 and route.missing <= 5 and route.probability >= 0.012:
                return route
            continue
        if route.missing <= 4 and route.probability >= 0.01:
            return route
        if route.missing <= 3 and route.probability >= 0.006:
            return route

    honitsu_routes = [
        route for route in estimates
        if route.name.startswith("honitsu_")
    ]
    honitsu_routes.sort(key=lambda route: route.value, reverse=True)
    for route in honitsu_routes:
        suit = _route_suit(route.name)
        allowed = suit | HONORS
        allowed_count = sum(counts[tile_id] for tile_id in allowed)
        off_count = sum(counts) - allowed_count
        if allowed_count >= 10 and off_count <= 3:
            return route
        if allowed_count >= 9 and balls >= 5 and off_count <= 4 and shanten(TileSet(counts)) <= 3:
            return route
    return None


def _suuankou_estimate(
    counts: tuple[int, ...],
    balls: int,
    areas: tuple[tuple[int, ...], ...],
) -> RouteEstimate | None:
    waits = _route_waits(counts, "suuankou")
    if waits:
        probability = _route_probability(counts, waits, 1, balls, areas)
        return RouteEstimate("suuankou", SUUANKOU_ROUTE_VALUE, 1, waits, probability)

    candidates = [tile_id for tile_id, count in enumerate(counts) if count]
    if len(candidates) < 5:
        candidates.extend(tile_id for tile_id in range(TILE_COUNT) if tile_id not in candidates)

    best_missing = 99
    best_targets: set[int] = set()
    for pair in candidates:
        pair_cost = max(0, 2 - counts[pair])
        triplet_options = sorted(
            (max(0, 3 - counts[tile_id]), tile_id)
            for tile_id in candidates
            if tile_id != pair
        )
        selected = tuple(tile_id for _, tile_id in triplet_options[:4])
        if len(selected) < 4:
            continue
        selected_costs = [cost for cost, _ in triplet_options[:4]]
        cost = pair_cost + sum(selected_costs)
        cutoff_cost = selected_costs[-1]
        targets = []
        if counts[pair] < 2:
            targets.append(pair)
        targets.extend(
            tile_id
            for option_cost, tile_id in triplet_options
            if option_cost <= cutoff_cost and counts[tile_id] < 3
        )
        if cost < best_missing:
            best_missing = cost
            best_targets = set(targets)
        elif cost == best_missing:
            best_targets.update(targets)
    if best_missing == 99:
        return None
    missing = best_missing
    targets = tuple(sorted(best_targets))
    probability = _route_probability(counts, targets, missing, balls, areas)
    return RouteEstimate("suuankou", SUUANKOU_ROUTE_VALUE, missing, targets, probability)


def _daisangen_estimate(
    counts: tuple[int, ...],
    balls: int,
    areas: tuple[tuple[int, ...], ...],
) -> RouteEstimate:
    dragon_missing = sum(max(0, 3 - counts[tile_id]) for tile_id in DRAGONS)
    targets = tuple(tile_id for tile_id in DRAGONS if counts[tile_id] < 3)
    shape_drag = max(0, shanten(TileSet(counts)) - 1)
    missing = dragon_missing + shape_drag
    probability = _route_probability(counts, targets, missing, balls, areas)
    return RouteEstimate("daisangen", DASANGEN_ROUTE_VALUE, missing, targets, probability)


def _chuuren_estimate(
    counts: tuple[int, ...],
    balls: int,
    areas: tuple[tuple[int, ...], ...],
    suit: frozenset[int],
) -> RouteEstimate:
    start = min(suit)
    base = [3, 1, 1, 1, 1, 1, 1, 1, 3]
    best_missing = 99
    best_targets: tuple[int, ...] = ()
    for extra_rank in range(9):
        needed = base[:]
        needed[extra_rank] += 1
        missing = 0
        targets = []
        for idx, need in enumerate(needed):
            tile_id = start + idx
            deficit = max(0, need - counts[tile_id])
            missing += deficit
            if deficit:
                targets.append(tile_id)
        missing += sum(counts[tile_id] for tile_id in range(TILE_COUNT) if tile_id not in suit)
        if missing < best_missing:
            best_missing = missing
            best_targets = tuple(targets)
    probability = _route_probability(counts, best_targets, best_missing, balls, areas)
    name = f"chuuren_{_suit_name(suit)}"
    return RouteEstimate(name, CHUUREN_ROUTE_VALUE, best_missing, best_targets, probability)


def _kokushi_estimate(
    counts: tuple[int, ...],
    balls: int,
    areas: tuple[tuple[int, ...], ...],
) -> RouteEstimate:
    terminals = TERMINAL_AND_HONOR_IDS
    missing_uniques = [tile_id for tile_id in terminals if counts[tile_id] == 0]
    has_pair = any(counts[tile_id] >= 2 for tile_id in terminals)
    pair_targets = [] if has_pair else [tile_id for tile_id in terminals if counts[tile_id] == 1]
    targets = tuple(sorted(set(missing_uniques + pair_targets)))
    missing = len(missing_uniques) + (0 if has_pair else 1)
    probability = _route_probability(counts, targets, missing, balls, areas)
    return RouteEstimate("kokushi", KOKUSHI_ROUTE_VALUE, missing, targets, probability)


def _honitsu_estimate(
    counts: tuple[int, ...],
    balls: int,
    areas: tuple[tuple[int, ...], ...],
    suit: frozenset[int],
) -> RouteEstimate:
    allowed = suit | HONORS
    allowed_count = sum(counts[tile_id] for tile_id in allowed)
    off_count = max(0, sum(counts) - allowed_count)
    suit_count = sum(counts[tile_id] for tile_id in suit)
    honor_pairs = sum(1 for tile_id in HONORS if counts[tile_id] >= 2)
    triplets = sum(1 for tile_id in allowed if counts[tile_id] >= 3)
    missing = max(0, off_count) + max(0, 8 - allowed_count)
    reward = 18.0 + 2.5 * allowed_count + 4.0 * honor_pairs + 3.0 * triplets
    if suit_count >= 8:
        reward += 10.0
    targets = tuple(tile_id for tile_id in allowed if counts[tile_id] < 4)
    probability = _route_probability(counts, targets, missing, balls, areas)
    return RouteEstimate(f"honitsu_{_suit_name(suit)}", reward, missing, targets, probability)


def _normal_efficiency_value(
    counts: tuple[int, ...],
    balls: int,
    areas: tuple[tuple[int, ...], ...],
) -> float:
    hand = TileSet(counts)
    waits = winning_tiles(hand)
    if waits:
        p = _best_area_probability(counts, waits, areas)
        return 18.0 * _at_least_one(balls, p)
    distance = max(0, shanten(hand) + 1)
    targets = tuple(tile_id for tile_id in range(TILE_COUNT) if counts[tile_id] < 4)
    p = _best_area_probability(counts, targets, areas)
    return 8.0 * _binomial_tail(balls, max(1, distance), p * 0.28)


def _route_probability(
    counts: tuple[int, ...],
    targets: tuple[int, ...],
    missing: int,
    balls: int,
    areas: tuple[tuple[int, ...], ...],
) -> float:
    if missing <= 0:
        return 1.0
    if balls <= 0 or not targets:
        return 0.0
    p = _best_area_probability(counts, targets, areas)
    return _binomial_tail(balls, missing, p)


def _best_area_probability(
    counts: tuple[int, ...],
    targets: tuple[int, ...],
    areas: tuple[tuple[int, ...], ...],
) -> float:
    target_set = set(targets)
    best = 0.0
    for weights in areas:
        valid_total = sum(weight for tile_id, weight in enumerate(weights) if counts[tile_id] < 4)
        if valid_total <= 0:
            continue
        hit = sum(
            weight
            for tile_id, weight in enumerate(weights)
            if tile_id in target_set and counts[tile_id] < 4
        )
        best = max(best, hit / valid_total)
    return best


def _conditioned_area_probability(
    counts: tuple[int, ...],
    targets: tuple[int, ...],
    area: int,
    areas: tuple[tuple[int, ...], ...],
) -> float:
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


def _yakuman_lookahead_discard(
    hand: TileSet,
    balls: int,
    route: RouteEstimate,
    areas: tuple[tuple[int, ...], ...],
) -> tuple[int, NextAreaEstimate]:
    route_candidates = []
    for tile_id in discard_options(hand):
        after = hand.with_removed_one(tile_id)
        post_route = _route_estimate_by_name(after.counts, balls, areas, route.name)
        if post_route is None:
            continue
        next_area = _next_area_estimate(after.counts, post_route.targets, areas)
        projected_probability = _binomial_tail(
            balls,
            post_route.missing,
            next_area.progress_probability,
        )
        route_candidates.append(
            (
                int(post_route.missing <= balls),
                -post_route.missing,
                projected_probability,
                tile_id,
                after,
                post_route,
                next_area,
            )
        )
    if not route_candidates:
        discard = min(
            discard_options(hand),
            key=lambda tile_id: (_route_keep_score(hand, tile_id, route), -tile_id),
        )
        after = hand.with_removed_one(discard)
        return discard, _next_area_estimate(after.counts, route.targets, areas)

    best_feasible = max(candidate[0] for candidate in route_candidates)
    feasible = [candidate for candidate in route_candidates if candidate[0] == best_feasible]
    best_missing = max(candidate[1] for candidate in feasible)
    shortest = [candidate for candidate in feasible if candidate[1] == best_missing]
    best_probability = max(candidate[2] for candidate in shortest)
    finalists = [
        candidate
        for candidate in shortest
        if abs(candidate[2] - best_probability) < 1e-12
    ]

    candidates = []
    for _, _, _, tile_id, _, _, next_area in finalists:
        candidates.append(
            (
                next_area.score,
                next_area.progress_probability,
                next_area.protection_probability,
                -_route_keep_score(hand, tile_id, route),
                tile_id,
                next_area,
            )
        )
    *_, discard, next_area = max(candidates)
    return discard, next_area


def _honitsu_lookahead_discard(
    hand: TileSet,
    balls: int,
    route: RouteEstimate,
    areas: tuple[tuple[int, ...], ...],
) -> tuple[int, NextAreaEstimate]:
    route_candidates = []
    for tile_id in discard_options(hand):
        after = hand.with_removed_one(tile_id)
        post_route = _route_estimate_by_name(after.counts, balls, areas, route.name)
        if post_route is None:
            continue
        route_candidates.append(
            (
                -post_route.missing,
                -shanten(after),
                tile_id,
                after,
                post_route,
            )
        )
    if not route_candidates:
        return _normal_lookahead_discard(hand, balls, areas)

    best_missing = max(candidate[0] for candidate in route_candidates)
    shortest = [candidate for candidate in route_candidates if candidate[0] == best_missing]
    best_shanten = max(candidate[1] for candidate in shortest)
    finalists = [candidate for candidate in shortest if candidate[1] == best_shanten]

    candidates = []
    for _, _, tile_id, after, post_route in finalists:
        targets = _honitsu_progress_targets(after, route.name)
        next_area = _next_area_estimate(after.counts, targets, areas)
        candidates.append(
            (
                next_area.score,
                next_area.progress_probability,
                post_route.probability,
                next_area.protection_probability,
                -_route_keep_score(hand, tile_id, route),
                tile_id,
                next_area,
            )
        )
    *_, discard, next_area = max(candidates)
    return discard, next_area


def _normal_lookahead_discard(
    hand: TileSet,
    balls: int,
    areas: tuple[tuple[int, ...], ...],
) -> tuple[int, NextAreaEstimate]:
    del balls
    shanten_candidates = []
    for tile_id in discard_options(hand):
        after = hand.with_removed_one(tile_id)
        shanten_candidates.append((-shanten(after), tile_id, after))

    best_shanten = max(candidate[0] for candidate in shanten_candidates)
    finalists = [
        candidate
        for candidate in shanten_candidates
        if candidate[0] == best_shanten
    ]

    candidates = []
    for _, tile_id, after in finalists:
        targets = improving_tiles(after)
        next_area = _next_area_estimate(after.counts, targets, areas)
        candidates.append(
            (
                next_area.score,
                next_area.progress_probability,
                next_area.protection_probability,
                len(targets),
                -_tile_keep_bias(hand, tile_id),
                tile_id,
                next_area,
            )
        )
    *_, discard, next_area = max(candidates)
    return discard, next_area


def _route_estimate_by_name(
    counts: tuple[int, ...],
    balls: int,
    areas: tuple[tuple[int, ...], ...],
    name: str,
) -> RouteEstimate | None:
    return next(
        (route for route in _route_estimates(counts, balls, areas) if route.name == name),
        None,
    )


def _honitsu_progress_targets(hand: TileSet, route_name: str) -> tuple[int, ...]:
    allowed = _route_suit(route_name) | HONORS
    efficient = tuple(
        tile_id
        for tile_id in improving_tiles(hand)
        if tile_id in allowed and hand.counts[tile_id] < 4
    )
    if efficient:
        return efficient
    return tuple(
        tile_id
        for tile_id in sorted(allowed)
        if hand.counts[tile_id] < 4
    )


def _yakuman_tenpai_discard(
    hand: TileSet,
    areas: tuple[tuple[int, ...], ...],
) -> tuple[int, tuple[int, ...]] | None:
    candidates = []
    for tile_id in discard_options(hand):
        after = hand.with_removed_one(tile_id)
        waits = _yakuman_waits(after.counts)
        if not waits:
            continue
        win_p = _best_area_probability(after.counts, waits, areas)
        protected = tuple(i for i, count in enumerate(after.counts) if count == 3)
        protect_p = _best_area_probability(after.counts, protected, areas) if protected else 0.0
        fourth_relief = int(hand.counts[tile_id] >= 4)
        keep_score = _tile_keep_bias(hand, tile_id)
        candidates.append(
            (
                win_p,
                len(waits),
                protect_p,
                fourth_relief,
                -keep_score,
                -tile_id,
                tile_id,
                waits,
            )
        )
    if not candidates:
        return None
    *_, discard, waits = max(candidates)
    return discard, waits


def _normal_tenpai_discard(
    hand: TileSet,
    balls: int,
    areas: tuple[tuple[int, ...], ...],
    *,
    dora_id: int | None,
    ura_dora_id: int | None,
    turn: int | None,
) -> TenpaiDiscardEstimate | None:
    estimates = []
    declare_riichi = balls <= 3
    double_reach = declare_riichi and turn == 1
    for tile_id in discard_options(hand):
        after = hand.with_removed_one(tile_id)
        waits = winning_tiles(after)
        if not waits:
            continue
        win_p = _best_area_probability(after.counts, waits, areas)
        wait_value = _wait_value(
            after,
            waits,
            areas,
            dora_id=dora_id,
            ura_dora_id=ura_dora_id,
            reach=declare_riichi,
            double_reach=double_reach,
        )
        value = _at_least_one(balls, win_p) * wait_value
        protected = tuple(i for i, count in enumerate(after.counts) if count == 3)
        protect_p = _best_area_probability(after.counts, protected, areas) if protected else 0.0
        keep_score = _tile_keep_bias(hand, tile_id)
        estimates.append(
            (
                value,
                win_p,
                len(waits),
                protect_p,
                -keep_score,
                -tile_id,
                TenpaiDiscardEstimate(
                    discard=tile_id,
                    waits=waits,
                    win_probability=win_p,
                    value=value,
                    declare_riichi=declare_riichi,
                ),
            )
        )
    if not estimates:
        return None
    return max(estimates)[-1]


def _should_keep_normal_tenpai(
    tenpai_value: float,
    route: RouteEstimate | None,
    balls: int,
) -> bool:
    if route is not None and not route.name.startswith("honitsu_"):
        if route.missing <= balls and route.value > tenpai_value * 1.05:
            return False
    if balls <= 2:
        return True
    if route is None or route.value <= 0:
        return True
    return route.value <= tenpai_value * 1.35


def _normal_tenpai_improvement_targets(
    hand: TileSet,
    balls: int,
    areas: tuple[tuple[int, ...], ...],
    *,
    dora_id: int | None,
    ura_dora_id: int | None,
) -> tuple[int, ...]:
    if balls < 4:
        return ()
    current_waits = winning_tiles(hand)
    if not current_waits:
        return ()
    current_value = _wait_value(
        hand,
        current_waits,
        areas,
        dora_id=dora_id,
        ura_dora_id=ura_dora_id,
        reach=False,
        double_reach=False,
    )
    targets = []
    for tile_id in range(TILE_COUNT):
        if tile_id in current_waits or not hand.can_add(tile_id):
            continue
        candidate = hand.with_added(tile_id)
        improved = _normal_tenpai_discard(
            candidate,
            max(1, balls - 1),
            areas,
            dora_id=dora_id,
            ura_dora_id=ura_dora_id,
            turn=None,
        )
        if improved is None:
            continue
        if set(improved.waits) != set(current_waits) and improved.value > current_value * 1.35:
            targets.append(tile_id)
    return tuple(targets)


def _wait_value(
    hand: TileSet,
    waits: tuple[int, ...],
    areas: tuple[tuple[int, ...], ...],
    *,
    dora_id: int | None,
    ura_dora_id: int | None,
    reach: bool,
    double_reach: bool,
) -> float:
    if not waits:
        return 0.0
    best_weights = max(
        areas,
        key=lambda weights: sum(weights[tile_id] for tile_id in waits if hand.counts[tile_id] < 4),
    )
    total = sum(best_weights[tile_id] for tile_id in waits if hand.counts[tile_id] < 4)
    if total <= 0:
        return 0.0
    value = 0.0
    for tile_id in waits:
        weight = best_weights[tile_id] if hand.counts[tile_id] < 4 else 0
        if weight <= 0:
            continue
        complete = hand.with_added(tile_id)
        value += (weight / total) * _complete_value_context(
            complete.counts,
            dora_id=dora_id,
            ura_dora_id=ura_dora_id,
            reach=reach,
            double_reach=double_reach,
            ippatsu=False,
        )
    return value


def _complete_value_context(
    counts: tuple[int, ...],
    *,
    dora_id: int | None,
    ura_dora_id: int | None,
    reach: bool,
    double_reach: bool,
    ippatsu: bool,
) -> float:
    score = score_hand(
        TileSet(counts),
        dora_id=dora_id,
        ura_dora_id=ura_dora_id,
        reach=reach,
        double_reach=double_reach,
        ippatsu=ippatsu,
    )
    payout = float(payout_for_score(score, bet=10))
    if score.yakuman_count:
        if score.han >= 13 and "kazoe_yakuman" in score.yaku:
            return max(COUNTED_YAKUMAN_COMPLETE_VALUE, payout + 90.0)
        return max(YAKUMAN_COMPLETE_VALUE * min(4, score.yakuman_count), payout + 120.0)
    return payout + _paren_entry_value(score)


def _route_tenpai_discard(
    hand: TileSet,
    route: RouteEstimate,
    areas: tuple[tuple[int, ...], ...],
) -> tuple[int, tuple[int, ...]] | None:
    candidates = []
    for tile_id in discard_options(hand):
        after = hand.with_removed_one(tile_id)
        waits = _route_waits(after.counts, route.name)
        if not waits:
            continue
        win_p = _best_area_probability(after.counts, waits, areas)
        protected = tuple(i for i, count in enumerate(after.counts) if count == 3)
        protect_p = _best_area_probability(after.counts, protected, areas) if protected else 0.0
        fourth_relief = int(hand.counts[tile_id] >= 4)
        keep_score = _route_keep_score(hand, tile_id, route)
        candidates.append(
            (
                win_p,
                len(waits),
                protect_p,
                fourth_relief,
                -keep_score,
                -tile_id,
                tile_id,
                waits,
            )
        )
    if not candidates:
        return None
    *_, discard, waits = max(candidates)
    return discard, waits


@lru_cache(maxsize=600_000)
def _route_waits(counts: tuple[int, ...], route_name: str) -> tuple[int, ...]:
    hand = TileSet(counts)
    waits = []
    yaku_name = _route_yaku_name(route_name)
    if yaku_name is None:
        return ()
    for tile_id in winning_tiles(hand):
        score = score_hand(hand.with_added(tile_id))
        if yaku_name in score.yaku and score.yakuman_count:
            waits.append(tile_id)
    return tuple(waits)


@lru_cache(maxsize=600_000)
def _yakuman_waits(counts: tuple[int, ...]) -> tuple[int, ...]:
    if not _yakuman_wait_gate(counts):
        return ()
    hand = TileSet(counts)
    waits = []
    for tile_id in winning_tiles(hand):
        if score_hand(hand.with_added(tile_id)).is_yakuman:
            waits.append(tile_id)
    return tuple(waits)


def _yakuman_wait_gate(counts: tuple[int, ...]) -> bool:
    if sum(counts) != 13:
        return False
    pairish = sum(1 for count in counts if count >= 2)
    triplets = sum(1 for count in counts if count >= 3)
    if triplets >= 3 and pairish >= 4:
        return True

    dragon_tiles = sum(counts[tile_id] for tile_id in DRAGONS)
    if dragon_tiles >= 7:
        return True

    terminal_unique = sum(1 for tile_id in TERMINAL_AND_HONOR_IDS if counts[tile_id])
    if terminal_unique >= 12:
        return True

    honor_tiles = sum(counts[tile_id] for tile_id in HONORS)
    if honor_tiles >= 12:
        return True

    green_tiles = sum(counts[tile_id] for tile_id in GREEN)
    if green_tiles >= 12:
        return True

    terminal_tiles = sum(
        count
        for tile_id, count in enumerate(counts)
        if tile_id < 27 and tile_id % 9 in (0, 8)
    )
    if terminal_tiles + honor_tiles >= 12:
        return True

    return any(sum(counts[tile_id] for tile_id in suit) >= 13 for suit in SUITS)


def _route_yaku_name(route_name: str) -> str | None:
    if route_name == "suuankou":
        return "suuankou"
    if route_name == "daisangen":
        return "daisangen"
    if route_name == "kokushi":
        return "kokushi"
    if route_name.startswith("chuuren_"):
        return "chuuren"
    return None


def _best_protection_bonus(counts: tuple[int, ...], areas: tuple[tuple[int, ...], ...]) -> float:
    protected = {tile_id for tile_id, count in enumerate(counts) if count == 3}
    if not protected:
        return 0.0
    best = 0.0
    for weights in areas:
        best = max(best, sum(weights[tile_id] for tile_id in protected) / EXPECTED_WEIGHT_SUM)
    return best


def _binomial_tail(trials: int, successes: int, p: float) -> float:
    if successes <= 0:
        return 1.0
    if trials < successes or p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    return sum(
        comb(trials, k) * (p**k) * ((1.0 - p) ** (trials - k))
        for k in range(successes, trials + 1)
    )


def _at_least_one(trials: int, p: float) -> float:
    if trials <= 0:
        return 0.0
    return 1.0 - ((1.0 - p) ** trials)


def _least_route_value_discard(hand: TileSet) -> int:
    return min(discard_options(hand), key=lambda tile_id: _tile_keep_bias(hand, tile_id))


def _route_keep_score(hand: TileSet, tile_id: int, route: RouteEstimate) -> float:
    count = hand.counts[tile_id]
    score = _tile_keep_bias(hand, tile_id)
    name = route.name

    if name == "daisangen":
        if tile_id in DRAGONS:
            score += 30.0
        elif count == 1 and tile_id not in HONORS:
            score -= 2.0
        score += _side_route_keep_bonus(hand, tile_id, route)
    elif name == "suuankou":
        if count >= 3:
            score += 20.0
        elif count == 2:
            score += 14.0
        elif count == 1 and tile_id not in HONORS:
            score -= 1.5
        score += _side_route_keep_bonus(hand, tile_id, route)
    elif name == "kokushi":
        if tile_id in TERMINAL_AND_HONOR_IDS:
            score += 18.0 if count == 1 else 4.0
        else:
            score -= 20.0
    elif name.startswith("chuuren_"):
        suit = _route_suit(name)
        if tile_id in suit:
            rank = tile_id % 9
            score += 22.0 if rank in (0, 8) else 14.0
        else:
            score -= 24.0
    elif name.startswith("honitsu_"):
        suit = _route_suit(name)
        if tile_id in suit:
            score += 14.0
        elif tile_id in HONORS:
            score += 10.0
            if count >= 2:
                score += 6.0
        else:
            score -= 18.0
    return score


def _tile_keep_bias(hand: TileSet, tile_id: int) -> float:
    count = hand.counts[tile_id]
    bias = count * 3.0
    if tile_id in DRAGONS:
        bias += 3.0
    if tile_id in HONORS:
        bias += 1.0
    if tile_id in TERMINAL_AND_HONOR_IDS:
        bias += 0.5
    return bias


def _side_route_keep_bonus(hand: TileSet, tile_id: int, route: RouteEstimate) -> float:
    side_suit = _side_route_suit(hand.counts, route)
    if side_suit is None:
        return 0.0

    count = hand.counts[tile_id]
    if tile_id in side_suit:
        bonus = 4.0
        if tile_id in route.targets:
            bonus += 4.0
        if count == 1:
            bonus += 2.0
        elif count >= 2:
            bonus += 3.0
        return bonus

    if tile_id in HONORS:
        bonus = 2.0
        if count >= 2:
            bonus += 4.0
        elif count == 1:
            bonus += 1.0
        return bonus

    penalty = -7.0 if count == 1 else -3.0
    if tile_id in TERMINAL_AND_HONOR_IDS:
        penalty -= 1.0
    return penalty


def _side_route_suit(
    counts: tuple[int, ...],
    route: RouteEstimate,
) -> frozenset[int] | None:
    target_suit = _route_target_suit(route)
    if target_suit is not None:
        return target_suit
    return _best_honitsu_fallback_suit(counts)


def _route_target_suit(route: RouteEstimate) -> frozenset[int] | None:
    scored = [
        (sum(1 for tile_id in route.targets if tile_id in suit), suit)
        for suit in SUITS
    ]
    hits, suit = max(scored, key=lambda item: item[0])
    return suit if hits >= 2 else None


def _best_honitsu_fallback_suit(counts: tuple[int, ...]) -> frozenset[int] | None:
    total = sum(counts)
    scored = []
    for suit in SUITS:
        allowed = suit | HONORS
        allowed_count = sum(counts[tile_id] for tile_id in allowed)
        suit_count = sum(counts[tile_id] for tile_id in suit)
        pairish = sum(1 for tile_id in allowed if counts[tile_id] >= 2)
        triplets = sum(1 for tile_id in allowed if counts[tile_id] >= 3)
        off_count = total - allowed_count
        score = (
            allowed_count * 1.5
            + suit_count * 0.6
            + pairish * 1.8
            + triplets * 2.0
            - off_count * 1.7
        )
        scored.append((score, allowed_count, suit_count, suit))

    scored.sort(reverse=True, key=lambda item: item[0])
    best_score, best_allowed, best_suit_count, best_suit = scored[0]
    second_score = scored[1][0]
    if best_allowed >= 8:
        return best_suit
    if best_suit_count >= 6 and best_score - second_score >= 2.0:
        return best_suit
    return None


def _route_suit(name: str) -> frozenset[int]:
    if name.endswith("_man"):
        return MAN
    if name.endswith("_sou"):
        return SOU
    return PIN


def _suit_name(suit: frozenset[int]) -> str:
    if suit == MAN:
        return "man"
    if suit == SOU:
        return "sou"
    return "pin"


@lru_cache(maxsize=1)
def _default_areas() -> tuple[tuple[int, ...], ...]:
    from janq_lab.assets.nyukyu import load_tables

    return load_tables()["nyukyu_base_table.bytes"].areas
