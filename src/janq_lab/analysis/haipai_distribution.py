"""Compare observed JanQ starting hands with a physical-wall baseline."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import argparse
import json
import random
from pathlib import Path
from typing import Any, Iterable

from janq_lab.assets.nyukyu import load_tables
from janq_lab.model.haipai import ObservedHaipaiSet, load_observed_normal_haipai, random_wall_hand
from janq_lab.model.hand import TERMINAL_AND_HONOR_IDS, TileSet, kokushi_shanten, shanten
from janq_lab.strategy.route_ev import _active_route


DRAGONS = (31, 32, 33)


@dataclass(frozen=True)
class HaipaiDistributionReport:
    source: str
    observed: dict[str, Any]
    baseline: dict[str, Any]
    observed_ignored_hands: int
    baseline_hands: int
    seed: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "observed": self.observed,
            "baseline": self.baseline,
            "observed_ignored_hands": self.observed_ignored_hands,
            "baseline_hands": self.baseline_hands,
            "seed": self.seed,
        }


def compare_haipai_distribution(
    path: str | Path,
    *,
    baseline_hands: int = 10000,
    seed: int = 1,
) -> HaipaiDistributionReport:
    if baseline_hands < 1:
        raise ValueError("baseline_hands must be positive")

    observed_set = load_observed_normal_haipai(path)
    rng = random.Random(seed)
    baseline = tuple(random_wall_hand(rng) for _ in range(baseline_hands))

    return HaipaiDistributionReport(
        source=observed_set.source,
        observed=summarize_haipai(observed_set.hands),
        baseline=summarize_haipai(baseline),
        observed_ignored_hands=observed_set.ignored_hands,
        baseline_hands=baseline_hands,
        seed=seed,
    )


def summarize_observed_set(observed_set: ObservedHaipaiSet) -> dict[str, Any]:
    return summarize_haipai(observed_set.hands)


def summarize_haipai(hands: Iterable[TileSet]) -> dict[str, Any]:
    hand_tuple = tuple(hands)
    count = len(hand_tuple)
    if count == 0:
        return _empty_summary()

    route_areas = load_tables()["nyukyu_base_table.bytes"].areas
    route_counts: Counter[str] = Counter()
    feature_sums: Counter[str] = Counter()
    feature_histograms: dict[str, Counter[int]] = {
        "pairish_types": Counter(),
        "exact_pairs": Counter(),
        "triplet_types": Counter(),
        "dragon_count": Counter(),
        "kokushi_shanten": Counter(),
        "standard_shanten": Counter(),
    }

    for hand in hand_tuple:
        features = _features(hand)
        for key, value in features.items():
            feature_sums[key] += value
            if key in feature_histograms:
                feature_histograms[key][value] += 1

        route = _active_route(hand.counts, 8, route_areas)
        route_counts[route.name if route is not None else "none"] += 1

    route_rates = {
        route: route_count / count
        for route, route_count in sorted(route_counts.items())
    }
    return {
        "hands": count,
        "means": {
            key: feature_sums[key] / count
            for key in sorted(feature_sums)
        },
        "histograms": {
            key: {str(bucket): value for bucket, value in sorted(hist.items())}
            for key, hist in sorted(feature_histograms.items())
        },
        "route_counts": dict(sorted(route_counts.items())),
        "route_rates": route_rates,
        "opener_rates": {
            "suuankou_user_gate": feature_sums["suuankou_user_gate"] / count,
            "daisangen_user_gate": feature_sums["daisangen_user_gate"] / count,
            "kokushi_close": feature_sums["kokushi_close"] / count,
        },
    }


def _empty_summary() -> dict[str, Any]:
    return {
        "hands": 0,
        "means": {},
        "histograms": {},
        "route_counts": {},
        "route_rates": {},
        "opener_rates": {},
    }


def _features(hand: TileSet) -> dict[str, int]:
    counts = hand.counts
    pairish = sum(1 for count in counts if count >= 2)
    exact_pairs = sum(1 for count in counts if count == 2)
    triplets = sum(1 for count in counts if count >= 3)
    quads = sum(1 for count in counts if count == 4)
    dragon_count = sum(counts[tile_id] for tile_id in DRAGONS)
    terminal_honor_count = sum(counts[tile_id] for tile_id in TERMINAL_AND_HONOR_IDS)
    terminal_honor_unique = sum(1 for tile_id in TERMINAL_AND_HONOR_IDS if counts[tile_id])
    k_shanten = kokushi_shanten(counts)

    return {
        "pairish_types": pairish,
        "exact_pairs": exact_pairs,
        "triplet_types": triplets,
        "quad_types": quads,
        "dragon_count": dragon_count,
        "terminal_honor_count": terminal_honor_count,
        "terminal_honor_unique": terminal_honor_unique,
        "standard_shanten": shanten(hand),
        "kokushi_shanten": k_shanten,
        "suuankou_user_gate": int(triplets >= 1 and exact_pairs >= 2),
        "daisangen_user_gate": int(dragon_count >= 3),
        "kokushi_close": int(k_shanten <= 4),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Analyze observed JanQ starting hand distribution.")
    parser.add_argument(
        "path",
        nargs="?",
        default="_runtime/logs/janq_events.jsonl",
        help="JanqProbe JSONL file.",
    )
    parser.add_argument("--baseline-hands", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = compare_haipai_distribution(
        args.path,
        baseline_hands=args.baseline_hands,
        seed=args.seed,
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return

    data = report.to_dict()
    print(f"source: {data['source']}")
    print(
        "observed hands: {hands} ignored={ignored}".format(
            hands=data["observed"]["hands"],
            ignored=data["observed_ignored_hands"],
        )
    )
    print(f"baseline hands: {data['baseline_hands']} seed={data['seed']}")
    print("observed opener_rates:", data["observed"]["opener_rates"])
    print("baseline opener_rates:", data["baseline"]["opener_rates"])
    print("observed route_rates:", data["observed"]["route_rates"])
    print("baseline route_rates:", data["baseline"]["route_rates"])


if __name__ == "__main__":
    main()
