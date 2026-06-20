"""Compare observed JanQ shot outcomes with the nyukyu probability table."""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from typing import Any

from janq_lab.assets.nyukyu import AREA_COUNT, EXPECTED_WEIGHT_SUM, NyukyuTable, load_tables
from janq_lab.probe.replay import replay_file
from janq_lab.tiles import TILE_COUNT, tile_name


@dataclass(frozen=True)
class AreaDistributionSummary:
    area: int
    shots: int
    impossible_observations: int
    chi_square: float
    top_observed: tuple[tuple[str, int, float, float], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "area": self.area,
            "shots": self.shots,
            "impossible_observations": self.impossible_observations,
            "chi_square": self.chi_square,
            "top_observed": [
                {
                    "tile": tile,
                    "count": count,
                    "observed": observed,
                    "expected": expected,
                }
                for tile, count, observed, expected in self.top_observed
            ],
        }


@dataclass(frozen=True)
class ShotDistributionReport:
    total_shots: int
    areas: tuple[AreaDistributionSummary, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_shots": self.total_shots,
            "areas": [area.to_dict() for area in self.areas],
        }


def compare_shot_distribution(path: str, table: NyukyuTable | None = None) -> ShotDistributionReport:
    nyukyu = table if table is not None else load_tables()["nyukyu_base_table.bytes"]
    replay = replay_file(path)
    counts = [[0 for _ in range(TILE_COUNT)] for _ in range(AREA_COUNT)]

    for hand in replay.hands:
        for shot in hand.shots:
            if shot.area is None or shot.pai_model is None:
                continue
            if 1 <= shot.area <= AREA_COUNT and 0 <= shot.pai_model < TILE_COUNT:
                counts[shot.area - 1][shot.pai_model] += 1

    summaries = []
    for area in range(1, AREA_COUNT + 1):
        observed = counts[area - 1]
        shots = sum(observed)
        expected_weights = nyukyu.weights_for_area(area)
        summaries.append(_summarize_area(area, observed, expected_weights, shots))

    return ShotDistributionReport(
        total_shots=sum(sum(area_counts) for area_counts in counts),
        areas=tuple(summaries),
    )


def _summarize_area(
    area: int,
    observed: list[int],
    expected_weights: tuple[int, ...],
    shots: int,
) -> AreaDistributionSummary:
    if shots == 0:
        return AreaDistributionSummary(area, 0, 0, 0.0, ())

    chi_square = 0.0
    impossible = 0
    for tile_id, count in enumerate(observed):
        weight = expected_weights[tile_id]
        if weight == 0:
            impossible += count
            continue
        expected_count = shots * weight / EXPECTED_WEIGHT_SUM
        chi_square += (count - expected_count) ** 2 / expected_count

    top = []
    for tile_id, count in enumerate(observed):
        if count <= 0:
            continue
        top.append(
            (
                tile_name(tile_id),
                count,
                count / shots,
                expected_weights[tile_id] / EXPECTED_WEIGHT_SUM,
            )
        )
    top.sort(key=lambda item: item[1], reverse=True)

    return AreaDistributionSummary(
        area=area,
        shots=shots,
        impossible_observations=impossible,
        chi_square=chi_square,
        top_observed=tuple(top[:10]),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compare observed shot outcomes with nyukyu tables.")
    parser.add_argument("path", help="Path to janq_events.jsonl")
    args = parser.parse_args(argv)
    report = compare_shot_distribution(args.path)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

