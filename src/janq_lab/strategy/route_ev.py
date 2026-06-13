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
    is_complete_hand,
    shanten,
    tile_set,
    winning_tiles,
)
from janq_lab.model.scoring import HONORS, PIN, SOU, JanqScore, score_hand
from janq_lab.strategy.greedy import AreaDecision, DiscardDecision
from janq_lab.strategy.public import choose_public_area, choose_public_discard
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


def choose_route_ev_area(
    hand: TileSet | list[int] | tuple[int, ...],
    table: NyukyuTable,
    balls: int,
) -> AreaDecision:
    state = hand if isinstance(hand, TileSet) else tile_set(hand)
    active_route = _active_route(state.counts, balls, table.areas)
    if active_route is None:
        return choose_public_area(state, table)
    if active_route.name.startswith("honitsu_"):
        return choose_public_area(state, table)
    winners = winning_tiles(state)
    protected = tuple(tile_id for tile_id, count in enumerate(state.counts) if count == 3)

    scored = []
    for area in range(1, AREA_COUNT + 1):
        progress_p = _conditioned_area_probability(state.counts, active_route.targets, area, table.areas)
        win_p = _conditioned_area_probability(state.counts, winners, area, table.areas)
        protect_p = _conditioned_area_probability(state.counts, protected, area, table.areas)
        score = progress_p + win_p * 0.35 + protect_p * 0.25
        scored.append((score, progress_p, win_p, protect_p, area))
    value, progress_p, win_p, protect_p, area = max(scored)
    return AreaDecision(
        area=area,
        target_tiles=active_route.targets,
        target_weight=sum(table.tile_weight(area, tile_id) for tile_id in active_route.targets),
        reason=(
            f"yakuman_route:{active_route.name}:"
            f"progress={progress_p:.3f}:win={win_p:.3f}:protect={protect_p:.3f}:v={value:.3f}"
        ),
    )


def choose_route_ev_discard(
    hand: TileSet | list[int] | tuple[int, ...],
    balls: int,
) -> DiscardDecision:
    state = hand if isinstance(hand, TileSet) else tile_set(hand)
    if is_complete_hand(state):
        return DiscardDecision(True, None, None, (), "complete_hand")
    if balls <= 0:
        discard = _least_route_value_discard(state)
        return DiscardDecision(False, discard, None, (), "no_balls")

    route = _active_route(state.counts, balls, _default_areas())
    if route is None:
        return choose_public_discard(state)
    if route.name.startswith("honitsu_"):
        return choose_public_discard(state)
    discard = min(
        discard_options(state),
        key=lambda tile_id: (_route_keep_score(state, tile_id, route), -tile_id),
    )
    after = state.with_removed_one(discard)
    return DiscardDecision(
        False,
        discard,
        shanten(after),
        winning_tiles(after),
        f"route_ev_discard:{route.name}",
    )


choose_route_ev_area.uses_context = True  # type: ignore[attr-defined]
choose_route_ev_discard.uses_context = True  # type: ignore[attr-defined]


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
    candidates = [tile_id for tile_id, count in enumerate(counts) if count]
    if len(candidates) < 5:
        candidates.extend(tile_id for tile_id in range(TILE_COUNT) if tile_id not in candidates)

    best: tuple[int, tuple[int, ...], int, tuple[int, ...]] | None = None
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
        cost = pair_cost + sum(max(0, 3 - counts[tile_id]) for tile_id in selected)
        targets = []
        if counts[pair] < 2:
            targets.append(pair)
        targets.extend(tile_id for tile_id in selected if counts[tile_id] < 3)
        item = (cost, selected, pair, tuple(sorted(set(targets))))
        if best is None or item < best:
            best = item
    if best is None:
        return None
    missing, _, _, targets = best
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
    elif name == "suuankou":
        if count >= 3:
            score += 20.0
        elif count == 2:
            score += 14.0
        elif count == 1 and tile_id not in HONORS:
            score -= 1.5
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
